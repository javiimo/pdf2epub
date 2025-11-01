"""Postprocessing utilities for math and table boxes.

This module filters, merges, and expands layout boxes produced by the layout
and table detectors. It also remaps labels to the pipeline-specific classes
(``mathblock`` and ``tableblock``).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, MutableSequence, Optional, Sequence, Tuple

from .layout import LayoutBox, LayoutResult

__all__ = [
    "PostprocessOptions",
    "postprocess_math_and_tables",
]


@dataclass(frozen=True)
class PostprocessOptions:
    """Configuration for box postprocessing.

    Attributes:
        min_area_px: Minimum area in pixels to keep a box.
        margin_pts: Margin in points to expand each box on all sides.
        dpi: Rendering DPI used to convert points to pixels.
        merge_overlaps: Whether to merge overlapping boxes of the same label.
        label_map: Mapping from detector labels to final labels (e.g., formula->mathblock).
    """

    min_area_px: int = 200
    margin_pts: float = 5.0
    dpi: int = 360
    merge_overlaps: bool = True
    label_map: Mapping[str, str] = None  # type: ignore[assignment]
    suppress_math_inside_tables: bool = True
    table_cover_threshold: float = 0.9
    # Suppress math that overlaps with text paragraphs (inline math)
    suppress_inline_math: bool = True
    # Consider a formula inline if the intersection with any text box
    # covers at least this fraction of the math box area.
    #
    # In practice, the layout model often draws relatively tight boxes for
    # inline equations that overlap surrounding text by roughly half their
    # area. A threshold around 0.5–0.7 is more robust than 0.9 for filtering
    # inline math while keeping display equations.
    inline_cover_threshold: float = 0.6

    def __post_init__(self) -> None:  # type: ignore[override]
        if self.label_map is None:
            object.__setattr__(self, "label_map", {"formula": "mathblock", "table": "tableblock"})


def _points_to_pixels(pts: float, dpi: int) -> int:
    return max(0, int(round(pts * (dpi / 72.0))))


def _expand_rect(rect: Tuple[int, int, int, int], margin: int) -> Tuple[int, int, int, int]:
    x, y, w, h = rect
    x2, y2 = x + w, y + h
    x -= margin
    y -= margin
    x2 += margin
    y2 += margin
    if x < 0:
        x = 0
    if y < 0:
        y = 0
    w = max(0, x2 - x)
    h = max(0, y2 - y)
    return x, y, w, h


def _intersects(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return False
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    return not (ax2 <= bx or bx2 <= ax or ay2 <= by or by2 <= ay)


def _merge_overlapping(rects: List[Tuple[int, int, int, int]]) -> List[Tuple[int, int, int, int]]:
    """Greedy merge of overlapping rectangles.

    Continues merging as long as any pair intersects.
    """
    changed = True
    current = rects[:]
    while changed and len(current) > 1:
        changed = False
        out: List[Tuple[int, int, int, int]] = []
        used = [False] * len(current)
        for i in range(len(current)):
            if used[i]:
                continue
            xi, yi, wi, hi = current[i]
            x1, y1 = xi, yi
            x2, y2 = xi + wi, yi + hi
            used[i] = True
            for j in range(i + 1, len(current)):
                if used[j]:
                    continue
                if _intersects(current[i], current[j]):
                    changed = True
                    used[j] = True
                    xj, yj, wj, hj = current[j]
                    x1 = min(x1, xj)
                    y1 = min(y1, yj)
                    x2 = max(x2, xj + wj)
                    y2 = max(y2, yj + hj)
            out.append((x1, y1, max(0, x2 - x1), max(0, y2 - y1)))
        current = out
    return current


def _intersection_area(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> int:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return 0
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    return iw * ih


def postprocess_math_and_tables(
    layout: LayoutResult,
    *,
    options: Optional[PostprocessOptions] = None,
) -> LayoutResult:
    """Filter/merge/expand detected math and table boxes and relabel them.

    Only boxes whose label is present in ``options.label_map`` are kept. Labels
    are remapped to the corresponding values (e.g., 'formula' -> 'mathblock').
    """
    opts = options or PostprocessOptions()
    margin_px = _points_to_pixels(opts.margin_pts, opts.dpi)

    # Group boxes by target label
    groups: Dict[str, List[Tuple[int, int, int, int]]] = {}
    scores: Dict[str, List[float]] = {}
    for b in layout.boxes:
        target = opts.label_map.get(b.label)
        if target is None:
            continue
        if b.width * b.height < opts.min_area_px:
            continue
        groups.setdefault(target, []).append((b.x, b.y, b.width, b.height))
        scores.setdefault(target, []).append(b.score)

    per_label_boxes: Dict[str, List[LayoutBox]] = {}
    for target_label, rects in groups.items():
        merged_rects = _merge_overlapping(rects) if opts.merge_overlaps else rects
        out: List[LayoutBox] = []
        for r in merged_rects:
            ex, ey, ew, eh = _expand_rect(r, margin_px)
            score = max(scores.get(target_label, [0.0]) or [0.0])
            out.append(LayoutBox(label=target_label, score=score, x=ex, y=ey, width=ew, height=eh))
        per_label_boxes[target_label] = out

    # Optionally suppress math blocks that are almost fully covered by a table
    if opts.suppress_math_inside_tables:
        math_list = per_label_boxes.get("mathblock", [])
        tables = per_label_boxes.get("tableblock", [])
        kept: List[LayoutBox] = []
        for m in math_list:
            m_area = m.width * m.height
            if m_area <= 0:
                continue
            covered = False
            m_rect = (m.x, m.y, m.width, m.height)
            for t in tables:
                inter = _intersection_area(m_rect, (t.x, t.y, t.width, t.height))
                if inter > 0 and (inter / float(m_area)) >= opts.table_cover_threshold:
                    covered = True
                    break
            if not covered:
                kept.append(m)
        per_label_boxes["mathblock"] = kept

    # Optionally suppress math boxes considered inline (overlapping text)
    if opts.suppress_inline_math:
        math_list = per_label_boxes.get("mathblock", [])
        if math_list:
            # Collect raw text-like rectangles from the original layout.
            # Some models label plain text as 'text' and headings as
            # 'paragraph_title' or 'title'. We consider these as text-like
            # to detect inline formulas overlapping normal flow.
            text_like_labels = {"text", "paragraph_title", "title"}
            text_rects: List[Tuple[int, int, int, int]] = [
                (b.x, b.y, b.width, b.height)
                for b in layout.boxes
                if (b.label in text_like_labels)
            ]
            if text_rects:
                kept_inline: List[LayoutBox] = []
                for m in math_list:
                    m_area = m.width * m.height
                    if m_area <= 0:
                        continue
                    m_rect = (m.x, m.y, m.width, m.height)
                    inline_like = False
                    for t in text_rects:
                        inter = _intersection_area(m_rect, t)
                        # Mark as inline if a significant fraction of the
                        # math box area overlaps any text-like region.
                        if inter > 0 and (inter / float(m_area)) >= opts.inline_cover_threshold:
                            inline_like = True
                            break
                    if not inline_like:
                        kept_inline.append(m)
                per_label_boxes["mathblock"] = kept_inline

    # Flatten back preserving a stable order: math then tables for readability
    final_list: List[LayoutBox] = []
    for label in ("mathblock", "tableblock"):
        final_list.extend(per_label_boxes.get(label, []))

    return LayoutResult(image_path=layout.image_path, page_index=layout.page_index, boxes=tuple(final_list))

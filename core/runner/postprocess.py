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

    processed_boxes: List[LayoutBox] = []
    for target_label, rects in groups.items():
        merged_rects = _merge_overlapping(rects) if opts.merge_overlaps else rects
        for idx, r in enumerate(merged_rects):
            ex, ey, ew, eh = _expand_rect(r, margin_px)
            # Pick a representative score: max of contributing class
            score = max(scores.get(target_label, [0.0]) or [0.0])
            processed_boxes.append(
                LayoutBox(label=target_label, score=score, x=ex, y=ey, width=ew, height=eh)
            )

    return LayoutResult(image_path=layout.image_path, page_index=layout.page_index, boxes=tuple(processed_boxes))


"""Per-page fallback policy for full-page rasterization.

This module decides whether a page should be rasterized fully based on the
coverage of postprocessed boxes (e.g., mathblock/tableblock) over the page
image, or the number of boxes exceeding a threshold.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

try:  # Pillow is optional at import-time; we guard at runtime
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover - optional import guard
    Image = None  # type: ignore[assignment]

from .layout import LayoutBox

__all__ = [
    "FallbackError",
    "FallbackOptions",
    "PageFallbackDecision",
    "compute_union_area",
    "compute_coverage_for_boxes",
    "decide_page_fallback",
]


class FallbackError(RuntimeError):
    """Raised when the fallback decision cannot be computed."""


@dataclass(frozen=True)
class FallbackOptions:
    """Thresholds used to trigger full-page rasterization.

    Attributes:
        coverage_threshold: Minimum fraction of page area covered by boxes to
            trigger the fallback, in [0.0, 1.0]. Default 0.40.
        count_threshold: Minimum number of boxes to trigger the fallback.
            Default 8.
        labels: Optional set of labels to consider. When None, all boxes are
            considered.
    """

    coverage_threshold: float = 0.40
    count_threshold: int = 8
    labels: Optional[Sequence[str]] = ("mathblock", "tableblock")


@dataclass(frozen=True)
class PageFallbackDecision:
    """Decision outcome for a single page."""

    should_rasterize_full: bool
    coverage: float
    box_count: int


def _intersects(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return False
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    return not (ax2 <= bx or bx2 <= ax or ay2 <= by or by2 <= ay)


def _merge_overlapping(rects: List[Tuple[int, int, int, int]]) -> List[Tuple[int, int, int, int]]:
    """Greedy union of overlapping rectangles to avoid double-counting area."""
    if not rects:
        return []
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


def compute_union_area(rects: Iterable[Tuple[int, int, int, int]]) -> int:
    """Compute exact union area of axis-aligned rectangles via line sweep.

    Rectangles are provided as (x, y, w, h). Negative sizes are treated as 0.
    """
    # Normalize to (x1, x2, y1, y2) and filter empty
    norm: List[Tuple[int, int, int, int]] = []
    for x, y, w, h in rects:
        if w <= 0 or h <= 0:
            continue
        x1, x2 = int(x), int(x + w)
        y1, y2 = int(y), int(y + h)
        if x2 <= x1 or y2 <= y1:
            continue
        norm.append((x1, x2, y1, y2))
    if not norm:
        return 0

    # Build events along X: (x, type, y1, y2) where type=+1 (enter) or -1 (exit)
    events: List[Tuple[int, int, int, int]] = []
    for x1, x2, y1, y2 in norm:
        events.append((x1, 1, y1, y2))
        events.append((x2, -1, y1, y2))
    events.sort()

    def y_union_length(intervals: List[Tuple[int, int]]) -> int:
        if not intervals:
            return 0
        intervals.sort()
        total = 0
        cy1, cy2 = intervals[0]
        for ny1, ny2 in intervals[1:]:
            if ny1 > cy2:
                total += max(0, cy2 - cy1)
                cy1, cy2 = ny1, ny2
            else:
                if ny2 > cy2:
                    cy2 = ny2
        total += max(0, cy2 - cy1)
        return total

    area = 0
    active: List[Tuple[int, int]] = []
    prev_x = events[0][0]
    i = 0
    while i < len(events):
        x = events[i][0]
        dx = x - prev_x
        if dx > 0 and active:
            area += dx * y_union_length(active[:])
        # Consume all events at this x
        while i < len(events) and events[i][0] == x:
            _, typ, y1, y2 = events[i]
            if typ == 1:
                active.append((y1, y2))
            else:
                # Remove one occurrence of (y1, y2)
                for j in range(len(active)):
                    if active[j] == (y1, y2):
                        del active[j]
                        break
            i += 1
        prev_x = x

    return area


def compute_coverage_for_boxes(
    image_size: Tuple[int, int],
    boxes: Sequence[LayoutBox],
    *,
    labels: Optional[Sequence[str]] = None,
) -> float:
    """Return fraction of page area covered by the given boxes.

    Args:
        image_size: (width, height) in pixels.
        boxes: Sequence of LayoutBox in page pixel coordinates.
        labels: If provided, only boxes with a label in this list are used.
    """
    width, height = image_size
    if width <= 0 or height <= 0:
        return 0.0
    rects: List[Tuple[int, int, int, int]] = []
    for b in boxes:
        if labels is not None and b.label not in labels:
            continue
        rects.append((b.x, b.y, b.width, b.height))
    union = compute_union_area(rects)
    total = int(width) * int(height)
    return max(0.0, min(1.0, (union / total) if total > 0 else 0.0))


def decide_page_fallback(
    image_path: Path,
    boxes: Sequence[LayoutBox],
    *,
    options: Optional[FallbackOptions] = None,
) -> PageFallbackDecision:
    """Decide if a page should be fully rasterized based on coverage or count.

    Uses the page image size to compute coverage of the provided boxes. By
    default, only ``mathblock`` and ``tableblock`` labels are considered.
    """
    opts = options or FallbackOptions()

    if Image is None:  # pragma: no cover - environment dependent
        raise FallbackError("Pillow (PIL) no disponible para leer el tamaño de la imagen.")

    image_path = Path(image_path)
    if not image_path.exists():
        raise FallbackError(f"La imagen de la página no existe: {image_path}")

    with Image.open(image_path) as im:  # type: ignore[union-attr]
        width, height = im.size  # (w, h)

    coverage = compute_coverage_for_boxes((width, height), boxes, labels=opts.labels)
    if opts.labels is None:
        box_count = len(boxes)
    else:
        box_count = sum(1 for b in boxes if b.label in opts.labels)

    should = (coverage >= float(opts.coverage_threshold)) or (box_count >= int(opts.count_threshold))
    return PageFallbackDecision(should_rasterize_full=should, coverage=coverage, box_count=box_count)

"""Coordinate mapping helpers between detector pixels and PDF points.

This module covers the "Mapeo de coordenadas" checklist items by
providing conversions that take page rotation into account and allow
callers to inflate regions with configurable margins.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - imported for typing only
    from .detect_prep import DetectedRegion, PageImage

__all__ = [
    "RectMargins",
    "page_scale",
    "pixels_to_pdf_rect",
    "region_pixels_to_pdf_rect",
    "expand_pdf_rect",
    "pdf_rect_to_pixels",
]


@dataclass(frozen=True)
class RectMargins:
    """Margins (in PDF points) to expand rectangles on each side."""

    left: float = 0.0
    right: float = 0.0
    top: float = 0.0
    bottom: float = 0.0

    @classmethod
    def uniform(cls, value: float) -> "RectMargins":
        """Create margins that apply the same value to every side."""

        return cls(left=value, right=value, top=value, bottom=value)


def page_scale(page: "PageImage") -> Tuple[float, float]:
    """Return the ``(scale_x, scale_y)`` pixels-per-point factors for ``page``."""

    return page.scale_x, page.scale_y


def _rotated_dimensions(page: "PageImage") -> Tuple[float, float]:
    return page.rotated_width_pts, page.rotated_height_pts


def _normalize_rotation(value: int) -> int:
    value = value % 360
    if value not in {0, 90, 180, 270}:
        raise ValueError(f"Rotación no soportada: {value}")
    return value


def _pixels_to_rotated_points(
    rect_px: Tuple[float, float, float, float],
    rotated_height_pts: float,
    scale_x: float,
    scale_y: float,
) -> Tuple[Tuple[float, float], ...]:
    x_px, y_px, w_px, h_px = rect_px
    if scale_x == 0 or scale_y == 0:
        return ((0.0, 0.0),) * 4

    left = x_px / scale_x
    right = (x_px + w_px) / scale_x
    top = rotated_height_pts - (y_px / scale_y)
    bottom = rotated_height_pts - ((y_px + h_px) / scale_y)
    return (
        (left, top),
        (right, top),
        (left, bottom),
        (right, bottom),
    )


def _inverse_rotate_point(
    point: Tuple[float, float],
    width_pts: float,
    height_pts: float,
    rotation: int,
) -> Tuple[float, float]:
    x, y = point
    if rotation == 0:
        return x, y
    if rotation == 90:
        return y, height_pts - x
    if rotation == 180:
        return width_pts - x, height_pts - y
    if rotation == 270:
        return width_pts - y, x
    raise ValueError(f"Rotación no soportada: {rotation}")


def _rotate_point(
    point: Tuple[float, float],
    width_pts: float,
    height_pts: float,
    rotation: int,
) -> Tuple[float, float]:
    x, y = point
    if rotation == 0:
        return x, y
    if rotation == 90:
        return height_pts - y, x
    if rotation == 180:
        return width_pts - x, height_pts - y
    if rotation == 270:
        return y, width_pts - x
    raise ValueError(f"Rotación no soportada: {rotation}")


def pixels_to_pdf_rect(
    page: "PageImage",
    rect_px: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
    """Convert a pixel-space rectangle into PDF coordinates (points)."""

    rotation = _normalize_rotation(page.rotate)
    width_pts, height_pts = page.width_pts, page.height_pts
    _, rot_height_pts = _rotated_dimensions(page)
    scale_x, scale_y = page_scale(page)
    corners_rot = _pixels_to_rotated_points(rect_px, rot_height_pts, scale_x, scale_y)

    # Convert the rotated coordinates back into the original PDF frame.
    rel_points = [_inverse_rotate_point(pt, width_pts, height_pts, rotation) for pt in corners_rot]

    xs = [pt[0] for pt in rel_points]
    ys = [pt[1] for pt in rel_points]
    crop_x0, crop_y0, crop_x1, crop_y1 = page.cropbox

    x0 = crop_x0 + min(xs)
    x1 = crop_x0 + max(xs)
    y0 = crop_y0 + min(ys)
    y1 = crop_y0 + max(ys)

    x0 = max(crop_x0, min(x0, crop_x1))
    x1 = min(crop_x1, max(x1, crop_x0))
    y0 = max(crop_y0, min(y0, crop_y1))
    y1 = min(crop_y1, max(y1, crop_y0))
    if x1 < x0:
        x1 = x0
    if y1 < y0:
        y1 = y0
    return (x0, y0, x1, y1)


def region_pixels_to_pdf_rect(
    page: "PageImage",
    region: "DetectedRegion",
    *,
    margins: RectMargins | float | None = None,
) -> Tuple[float, float, float, float]:
    """Helper to convert a :class:`DetectedRegion` bbox into PDF points."""

    rect = (float(region.x), float(region.y), float(region.width), float(region.height))
    rect_pt = pixels_to_pdf_rect(page, rect)
    if margins is None:
        return rect_pt
    if isinstance(margins, (int, float)):
        margins = RectMargins.uniform(float(margins))
    return expand_pdf_rect(rect_pt, margins=margins, cropbox=page.cropbox)


def expand_pdf_rect(
    rect: Tuple[float, float, float, float],
    *,
    margins: RectMargins,
    cropbox: Tuple[float, float, float, float] | None = None,
) -> Tuple[float, float, float, float]:
    """Expand ``rect`` by ``margins`` in PDF space and clamp to ``cropbox``."""

    x0, y0, x1, y1 = rect
    expanded = (
        x0 - margins.left,
        y0 - margins.bottom,
        x1 + margins.right,
        y1 + margins.top,
    )
    if cropbox is None:
        return expanded

    cx0, cy0, cx1, cy1 = cropbox
    ex0 = min(max(expanded[0], cx0), cx1)
    ey0 = min(max(expanded[1], cy0), cy1)
    ex1 = max(min(expanded[2], cx1), cx0)
    ey1 = max(min(expanded[3], cy1), cy0)
    return (min(ex0, ex1), min(ey0, ey1), max(ex0, ex1), max(ey0, ey1))


def pdf_rect_to_pixels(
    page: "PageImage",
    rect_pt: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
    """Convert a PDF-space rectangle into PNG pixel coordinates."""

    rotation = _normalize_rotation(page.rotate)
    width_pts, height_pts = page.width_pts, page.height_pts
    _, rot_height_pts = _rotated_dimensions(page)
    scale_x, scale_y = page_scale(page)

    crop_x0, crop_y0, _, _ = page.cropbox
    x0, y0, x1, y1 = rect_pt
    rel_points = [
        (x0 - crop_x0, y0 - crop_y0),
        (x1 - crop_x0, y0 - crop_y0),
        (x0 - crop_x0, y1 - crop_y0),
        (x1 - crop_x0, y1 - crop_y0),
    ]
    rotated = [_rotate_point(pt, width_pts, height_pts, rotation) for pt in rel_points]
    xs = [pt[0] for pt in rotated]
    ys = [pt[1] for pt in rotated]

    left = min(xs)
    right = max(xs)
    bottom = min(ys)
    top = max(ys)

    x_px = left * scale_x
    width_px = (right - left) * scale_x
    y_px = (rot_height_pts - top) * scale_y
    height_px = (top - bottom) * scale_y
    return (x_px, y_px, width_px, height_px)

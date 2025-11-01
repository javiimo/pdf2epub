from __future__ import annotations

from pathlib import Path

import pytest

from core.runner.coordinates import (
    RectMargins,
    expand_pdf_rect,
    page_scale,
    pdf_rect_to_pixels,
    pixels_to_pdf_rect,
    region_pixels_to_pdf_rect,
)
from core.runner.detect_prep import DetectedRegion, PageImage


def _make_page(
    *,
    rotate: int = 0,
    cropbox: tuple[float, float, float, float] = (0.0, 0.0, 200.0, 300.0),
    width_px: int = 1000,
    height_px: int = 1500,
) -> PageImage:
    return PageImage(
        page_index=1,
        dpi=360,
        image_path=Path("page.png"),
        width_px=width_px,
        height_px=height_px,
        cropbox=cropbox,
        mediabox=cropbox,
        rotate=rotate,
    )


def test_page_scale_accounts_for_rotation() -> None:
    page0 = _make_page()
    sx0, sy0 = page_scale(page0)
    assert sx0 == pytest.approx(5.0)
    assert sy0 == pytest.approx(5.0)

    rotated = _make_page(
        rotate=90,
        cropbox=(10.0, 20.0, 210.0, 320.0),
        width_px=1500,
        height_px=1000,
    )
    sx, sy = page_scale(rotated)
    assert sx == pytest.approx(5.0)
    assert sy == pytest.approx(5.0)


def test_pixels_to_pdf_rect_without_rotation() -> None:
    page = _make_page()
    rect = pixels_to_pdf_rect(page, (100.0, 150.0, 200.0, 300.0))
    assert rect == pytest.approx((20.0, 210.0, 60.0, 270.0))


def test_pixels_to_pdf_rect_with_rotation_90() -> None:
    page = _make_page(
        rotate=90,
        cropbox=(10.0, 20.0, 210.0, 320.0),
        width_px=1500,
        height_px=1000,
    )
    rect = pixels_to_pdf_rect(page, (50.0, 80.0, 150.0, 60.0))
    assert rect == pytest.approx((182.0, 280.0, 194.0, 310.0))


def test_pdf_rect_to_pixels_roundtrip() -> None:
    page = _make_page(
        rotate=270,
        cropbox=(5.0, 15.0, 205.0, 315.0),
        width_px=1500,
        height_px=1000,
    )
    original = (30.0, 40.0, 120.0, 150.0)
    rect_pt = pixels_to_pdf_rect(page, original)
    recon = pdf_rect_to_pixels(page, rect_pt)
    assert recon == pytest.approx(original)


def test_expand_pdf_rect_applies_margins_and_clamp() -> None:
    rect = (5.0, 5.0, 45.0, 45.0)
    margins = RectMargins(left=10.0, right=10.0, top=15.0, bottom=20.0)
    crop = (0.0, 0.0, 50.0, 50.0)
    expanded = expand_pdf_rect(rect, margins=margins, cropbox=crop)
    assert expanded == pytest.approx((0.0, 0.0, 50.0, 50.0))


def test_region_pixels_to_pdf_rect_with_uniform_margin() -> None:
    page = _make_page()
    region = DetectedRegion(
        page_index=1,
        label="math",
        score=0.9,
        x=100,
        y=150,
        width=200,
        height=300,
        source="layout",
    )
    base = region_pixels_to_pdf_rect(page, region)
    assert base == pytest.approx((20.0, 210.0, 60.0, 270.0))

    expanded = region_pixels_to_pdf_rect(page, region, margins=5.0)
    assert expanded == pytest.approx((15.0, 205.0, 65.0, 275.0))

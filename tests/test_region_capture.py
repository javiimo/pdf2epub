import json
import math
import zlib
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

from core.runner.region_capture import (
    CaptureOptions,
    RegionCaptureError,
    RegionSpec,
    capture_pdf_regions,
)


def _make_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    shape = page.new_shape()
    shape.draw_rect(fitz.Rect(10, 20, 110, 120))
    shape.finish(color=(0, 0, 0), fill=(1, 0, 0))
    shape.commit()
    doc.save(path)
    doc.close()


def test_capture_region_writes_png_and_metadata(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf)

    regions = [RegionSpec(page_index=1, rect_pt=(10, 20, 110, 120), label="math")]
    out_dir = tmp_path / "regions"
    captures = capture_pdf_regions(
        pdf,
        regions,
        output_dir=out_dir,
        options=CaptureOptions(dpi=360, transparent=False, image_prefix="part"),
    )

    assert len(captures) == 1
    capture = captures[0]
    assert capture.image_path.exists()
    assert capture.metadata_path.exists()

    rect = regions[0].rect_pt
    expected_w = math.ceil((rect[2] - rect[0]) * 360 / 72)
    expected_h = math.ceil((rect[3] - rect[1]) * 360 / 72)
    assert abs(capture.width_px - expected_w) <= 2
    assert abs(capture.height_px - expected_h) <= 2

    meta = json.loads(capture.metadata_path.read_text(encoding="utf-8"))
    assert meta["page_index"] == 1
    assert meta["label"] == "math"
    assert meta["dpi"] == 360
    assert meta["transparent"] is False

    color_data = zlib.decompress(capture.xobject.stream)
    assert len(color_data) == capture.width_px * capture.height_px * 3
    assert capture.xobject.soft_mask is None


def test_capture_region_supports_transparency(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf)

    regions = [RegionSpec(page_index=1, rect_pt=(10, 20, 110, 120), label="table")]
    captures = capture_pdf_regions(
        pdf,
        regions,
        output_dir=tmp_path / "regions",
        options=CaptureOptions(dpi=300, transparent=True),
    )

    capture = captures[0]
    mask = capture.xobject.soft_mask
    assert mask is not None
    alpha = zlib.decompress(mask.stream)
    assert len(alpha) == capture.width_px * capture.height_px


def test_capture_region_validates_inputs(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    _make_pdf(pdf)

    with pytest.raises(RegionCaptureError):
        capture_pdf_regions(
            pdf,
            [RegionSpec(page_index=1, rect_pt=(10, 10, 5, 20))],
            output_dir=tmp_path / "regions",
        )

    with pytest.raises(RegionCaptureError):
        capture_pdf_regions(
            pdf,
            [RegionSpec(page_index=1, rect_pt=(10, 10, 40, 40))],
            output_dir=tmp_path / "regions",
            options=CaptureOptions(dpi=200),
        )


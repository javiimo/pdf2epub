from __future__ import annotations

from pathlib import Path
import struct
import zlib

import pytest

fitz = pytest.importorskip("fitz")

from core.runner.detect_prep import (
    DetectionPrepOptions,
    DetectedRegion,
    PageImage,
    mark_preexisting_raster_regions,
    prepare_page_images,
    run_detectors_on_pages,
)
from core.runner.layout import LayoutBox, LayoutResult
from core.runner.tables import TableBox


def _create_pdf(path: Path, *, width: float = 200, height: float = 300) -> None:
    doc = fitz.open()
    doc.new_page(width=width, height=height)
    doc.save(path)
    doc.close()


def _write_png(path: Path, size: tuple[int, int]) -> None:
    width, height = size
    path.parent.mkdir(parents=True, exist_ok=True)
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(
        ">I", 13
    ) + b"IHDR" + struct.pack(
        ">II", width, height
    ) + b"\x08\x02\x00\x00\x00"
    ihdr_crc = struct.pack(
        ">I", zlib.crc32(ihdr_data[4:]) & 0xFFFFFFFF
    )
    row = b"\x00" + (b"\x00\x00\x00" * width)
    raw = row * height
    compressed = zlib.compress(raw)
    idat_data = struct.pack(
        ">I", len(compressed)
    ) + b"IDAT" + compressed
    idat_crc = struct.pack(
        ">I", zlib.crc32(idat_data[4:]) & 0xFFFFFFFF
    )
    iend = struct.pack(
        ">I", 0
    ) + b"IEND" + struct.pack(
        ">I", zlib.crc32(b"IEND") & 0xFFFFFFFF
    )
    with path.open("wb") as fh:
        fh.write(signature + ihdr_data + ihdr_crc + idat_data + idat_crc + iend)


def test_prepare_page_images_collects_metadata(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    _create_pdf(pdf, width=200, height=300)

    def fake_rasterize(input_pdf: Path, prefix: Path, dpi: int, run=None) -> Path:
        png = prefix.parent / f"{prefix.name}-1.png"
        _write_png(png, (400, 600))
        return prefix

    out_dir = tmp_path / "detectors"
    pages = prepare_page_images(pdf, out_dir, dpi=360, rasterize=fake_rasterize)

    assert len(pages) == 1
    page = pages[0]
    assert page.page_index == 1
    assert page.width_px == 400 and page.height_px == 600
    assert page.cropbox == (0.0, 0.0, 200.0, 300.0)
    assert page.mediabox == (0.0, 0.0, 200.0, 300.0)
    assert page.rotate == 0
    assert pytest.approx(page.scale_x, rel=1e-6) == 2.0
    assert pytest.approx(page.scale_y, rel=1e-6) == 2.0


def test_run_detectors_on_pages_filters_and_labels(tmp_path: Path) -> None:
    image_path = tmp_path / "page.png"
    _write_png(image_path, (300, 300))
    page = PageImage(
        page_index=1,
        dpi=360,
        image_path=image_path,
        width_px=300,
        height_px=300,
        cropbox=(0.0, 0.0, 300.0, 300.0),
        mediabox=(0.0, 0.0, 300.0, 300.0),
        rotate=0,
    )

    layout_boxes = (
        LayoutBox(label="formula", score=0.9, x=10, y=12, width=60, height=80),
        LayoutBox(label="table", score=0.5, x=150, y=160, width=20, height=20),
        LayoutBox(label="text", score=0.8, x=0, y=0, width=10, height=10),
    )
    layout_result = LayoutResult(image_path=image_path, page_index=1, boxes=layout_boxes)
    tables = (TableBox(label="table", score=1.0, x=140, y=150, width=60, height=60),)

    regions = run_detectors_on_pages(
        [page],
        options=DetectionPrepOptions(min_area_px=200, min_side_px=10, table_iou_threshold=0.2),
        layout_runner=lambda _: layout_result,
        table_runner=lambda _: tables,
    )

    labels = {r.label for r in regions}
    assert labels == {"math", "table"}
    math = next(r for r in regions if r.label == "math")
    table = next(r for r in regions if r.label == "table")
    assert math.source == "layout"
    assert table.source == "table"
    assert math.width == 60 and math.height == 80
    assert table.width >= 60 and table.height >= 60


def test_mark_preexisting_raster_regions_detects_overlap(tmp_path: Path) -> None:
    pdf = tmp_path / "with_image.pdf"
    img_path = tmp_path / "img.png"
    _write_png(img_path, (20, 20))

    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.insert_image(fitz.Rect(50, 60, 110, 120), filename=str(img_path))
    doc.save(pdf)
    doc.close()

    page_img = PageImage(
        page_index=1,
        dpi=360,
        image_path=tmp_path / "page.png",
        width_px=200,
        height_px=200,
        cropbox=(0.0, 0.0, 200.0, 200.0),
        mediabox=(0.0, 0.0, 200.0, 200.0),
        rotate=0,
    )

    overlapping = DetectedRegion(
        page_index=1,
        label="table",
        score=0.9,
        x=50,
        y=80,
        width=60,
        height=60,
        source="table",
    )
    separate = DetectedRegion(
        page_index=1,
        label="math",
        score=0.8,
        x=10,
        y=10,
        width=20,
        height=20,
        source="layout",
    )

    marked = mark_preexisting_raster_regions(
        pdf,
        [page_img],
        [overlapping, separate],
        iou_threshold=0.5,
    )

    assert marked[0].skip_due_to_image is True
    assert marked[1].skip_due_to_image is False


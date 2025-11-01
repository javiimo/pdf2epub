from __future__ import annotations

import json
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

from core.runner.pdf_insertion import (
    ImageInsertOptions,
    ImageInsertionError,
    ImageInsertSpec,
    insert_pdf_images,
)
from core.runner.region_capture import RegionSpec, capture_pdf_regions


def _make_source_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=220, height=220)
    rect = fitz.Rect(40, 60, 180, 200)
    shape = page.new_shape()
    shape.draw_rect(rect)
    shape.finish(color=(0, 0, 0), fill=(1, 0, 0))
    shape.commit()
    page.insert_text(fitz.Point(60, 90), "E = mc^2")
    doc.save(path)
    doc.close()


def _make_target_pdf(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=220, height=220)
    page.draw_rect(page.rect, color=(0, 0, 0), fill=(0.9, 0.9, 0.9), overlay=True)
    doc.save(path)
    doc.close()


def test_insert_images_embeds_xobject_and_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    target = tmp_path / "target.pdf"
    captures_dir = tmp_path / "captures"
    _make_source_pdf(source)
    _make_target_pdf(target)

    regions = [RegionSpec(page_index=1, rect_pt=(40, 60, 180, 200), label="math")]
    captures = capture_pdf_regions(source, regions, output_dir=captures_dir)
    spec = ImageInsertSpec(capture=captures[0])

    results = insert_pdf_images(target, [spec])

    assert len(results) == 1
    result = results[0]

    doc = fitz.open(target)
    try:
        page = doc[0]
        rect = fitz.Rect(*spec.capture.rect_pt)

        images = page.get_images(full=True)
        assert any(info[0] == result.image_xref for info in images)

        drawings = page.get_drawings()
        assert any(
            drawing.get("fill") == (1.0, 1.0, 1.0)
            and fitz.Rect(drawing.get("rect")).intersects(rect)
            for drawing in drawings
        )

        meta = doc.xref_get_key(result.image_xref, "/PDF2EPUBMetadata")
        assert meta is not None
        assert meta[0] == "string"
        payload = json.loads(meta[1])
        assert payload["label"] == "math"
        assert payload["page_index"] == 1
    finally:
        doc.close()


def test_insert_images_can_skip_background_and_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    target = tmp_path / "target.pdf"
    captures_dir = tmp_path / "captures"
    _make_source_pdf(source)
    _make_target_pdf(target)

    regions = [RegionSpec(page_index=1, rect_pt=(40, 60, 180, 200), label="table")]
    captures = capture_pdf_regions(source, regions, output_dir=captures_dir)
    spec = ImageInsertSpec(capture=captures[0], metadata={"note": "skip-bg"})

    results = insert_pdf_images(
        target,
        [spec],
        options=ImageInsertOptions(paint_background=False, metadata_key=None),
    )

    assert len(results) == 1
    result = results[0]

    doc = fitz.open(target)
    try:
        page = doc[0]
        drawings = page.get_drawings()
        assert all(drawing.get("fill") != (1.0, 1.0, 1.0) for drawing in drawings)
        meta = doc.xref_get_key(result.image_xref, "/PDF2EPUBMetadata")
        assert meta is None or meta[0] == "null"
    finally:
        doc.close()


def test_insert_images_validates_rectangles(tmp_path: Path) -> None:
    source = tmp_path / "source.pdf"
    target = tmp_path / "target.pdf"
    captures_dir = tmp_path / "captures"
    _make_source_pdf(source)
    _make_target_pdf(target)

    regions = [RegionSpec(page_index=1, rect_pt=(40, 60, 180, 200), label="math")]
    captures = capture_pdf_regions(source, regions, output_dir=captures_dir)
    bad_spec = ImageInsertSpec(capture=captures[0], rect_pt=(10, 20, 10, 50))

    with pytest.raises(ImageInsertionError):
        insert_pdf_images(target, [bad_spec])


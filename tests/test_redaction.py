from __future__ import annotations

import io
from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")

from PIL import Image

from core.runner.redaction import (
    RedactionOptions,
    RedactionRegion,
    RedactionResult,
    redact_pdf_regions,
)


def _make_pdf_with_text_and_lines(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    # Draw a rectangle resembling a table cell
    shape = page.new_shape()
    shape.draw_rect(fitz.Rect(40, 40, 260, 160))
    shape.draw_line(fitz.Point(40, 100), fitz.Point(260, 100))
    shape.draw_line(fitz.Point(150, 40), fitz.Point(150, 160))
    shape.finish(color=(0, 0, 0))
    shape.commit()
    page.insert_text(fitz.Point(60, 80), "E=mc^2")
    doc.save(path)
    doc.close()


def _make_pdf_with_image(path: Path) -> None:
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    img = Image.new("RGB", (80, 80), color="red")
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    page.insert_image(fitz.Rect(40, 60, 120, 140), stream=buffer.getvalue())
    page.insert_text(fitz.Point(45, 150), "Tabla")
    doc.save(path)
    doc.close()


def _words_in_rect(page: "fitz.Page", rect: "fitz.Rect") -> list[str]:
    words = []
    for block in page.get_text("rawdict").get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                bbox = span.get("bbox")
                if not bbox:
                    continue
                span_rect = fitz.Rect(*bbox)
                if span_rect.intersects(rect) and span.get("text"):
                    words.append(span["text"])
    return words


def _drawings_in_rect(page: "fitz.Page", rect: "fitz.Rect") -> int:
    count = 0
    for drawing in page.get_drawings():
        bbox = drawing.get("rect")
        if bbox is None:
            continue
        if fitz.Rect(bbox).intersects(rect):
            count += 1
    return count


def test_redact_removes_text_and_vectors(tmp_path: Path) -> None:
    pdf = tmp_path / "math.pdf"
    _make_pdf_with_text_and_lines(pdf)

    regions = [RedactionRegion(page_index=1, rect_pt=(30, 30, 270, 170), label="math")]
    results = redact_pdf_regions(pdf, regions)

    assert isinstance(results, tuple)
    assert len(results) == 1
    result = results[0]
    assert isinstance(result, RedactionResult)
    assert result.used_fallback is False

    doc = fitz.open(pdf)
    page = doc[0]
    rect = fitz.Rect(*regions[0].rect_pt)
    assert _words_in_rect(page, rect) == []
    assert _drawings_in_rect(page, rect) == 0
    doc.close()


def test_redact_uses_fallback_for_tables(tmp_path: Path) -> None:
    pdf = tmp_path / "table.pdf"
    _make_pdf_with_image(pdf)

    regions = [RedactionRegion(page_index=1, rect_pt=(30, 50, 130, 150), label="table")]
    results = redact_pdf_regions(pdf, regions, options=RedactionOptions(fallback_labels=("table",)))

    assert len(results) == 1
    assert results[0].used_fallback is True

    doc = fitz.open(pdf)
    page = doc[0]
    rect = fitz.Rect(*regions[0].rect_pt)
    raw = page.get_text("rawdict")
    for block in raw.get("blocks", []):
        bbox = block.get("bbox")
        if bbox is None:
            continue
        if fitz.Rect(*bbox).intersects(rect):
            pytest.fail("Redaction fallback should remove image blocks inside the region")
    assert _drawings_in_rect(page, rect) == 0
    doc.close()


def test_invalid_region_coordinates_raise(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    _make_pdf_with_text_and_lines(pdf)

    with pytest.raises(Exception):
        redact_pdf_regions(pdf, [RedactionRegion(page_index=1, rect_pt=(10, 20, 10, 40))])


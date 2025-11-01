from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence, Tuple

import pytest

fitz = pytest.importorskip("fitz")

from core.runner.coordinates import pixels_to_pdf_rect
from core.runner.detect_prep import prepare_page_images
from core.runner.layout import infer_layout_on_image
from core.runner.pdf_insertion import ImageInsertResult, ImageInsertSpec, insert_pdf_images
from core.runner.postprocess import PostprocessOptions, postprocess_math_and_tables
from core.runner.redaction import RedactionRegion, redact_pdf_regions
from core.runner.region_capture import CapturedRegionImage, RegionSpec, capture_pdf_regions
from core.runner.tables import fuse_tables_with_layout, infer_tables_on_image


MASTERING_PDF = Path(__file__).resolve().parents[1] / "Mastering.pdf"
PADDLE = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "paddleocr"


def _subset_pdf(source: Path, target: Path, page_index: int) -> None:
    doc = fitz.open(str(source))
    try:
        doc.select([page_index - 1])
        doc.save(str(target))
    finally:
        doc.close()


def _words_in_rect(page: "fitz.Page", rect: "fitz.Rect") -> Sequence[str]:
    words: list[str] = []
    raw = page.get_text("rawdict")
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                bbox = span.get("bbox")
                text = span.get("text", "")
                if not bbox or not text:
                    continue
                span_rect = fitz.Rect(*bbox)
                if span_rect.intersects(rect):
                    words.append(text)
    return tuple(words)


def _detect_regions(page_image, *, paddleocr_path: str) -> Sequence[Tuple[str, Tuple[float, float, float, float]]]:
    layout = infer_layout_on_image(
        page_image.image_path,
        device="cpu",
        paddleocr_path=paddleocr_path,
    )
    tables = infer_tables_on_image(
        page_image.image_path,
        device="cpu",
        paddleocr_path=paddleocr_path,
    )
    fused = fuse_tables_with_layout(layout, tables, iou_threshold=0.3)
    post = postprocess_math_and_tables(
        fused,
        options=PostprocessOptions(
            min_area_px=150,
            dpi=page_image.dpi,
            margin_pts=5.0,
        ),
    )

    regions: list[Tuple[str, Tuple[float, float, float, float]]] = []
    for box in post.boxes:
        if box.label == "mathblock":
            label = "math"
        elif box.label == "tableblock":
            label = "table"
        else:
            continue
        rect_pt = pixels_to_pdf_rect(page_image, (box.x, box.y, box.width, box.height))
        regions.append((label, rect_pt))
    return tuple(regions)


def _run_pipeline_for_page(
    tmp_path: Path, page_number: int
) -> tuple[Path, tuple[CapturedRegionImage, ...], tuple[ImageInsertResult, ...]]:
    subset_pdf = tmp_path / f"page-{page_number}.pdf"
    _subset_pdf(MASTERING_PDF, subset_pdf, page_number)

    detectors_dir = tmp_path / f"detectors-{page_number}"
    pages = prepare_page_images(subset_pdf, detectors_dir, dpi=360)
    if not pages:
        raise RuntimeError("No se generaron imágenes de página para los detectores")
    page_image = pages[0]

    regions = _detect_regions(page_image, paddleocr_path=str(PADDLE))
    if not regions:
        raise RuntimeError("No se detectaron regiones a rasterizar en la página")

    region_specs = [
        RegionSpec(page_index=page_image.page_index, rect_pt=rect_pt, label=label)
        for label, rect_pt in regions
    ]
    captures = capture_pdf_regions(
        subset_pdf,
        region_specs,
        output_dir=tmp_path / f"captures-{page_number}",
    )

    redaction_regions = [
        RedactionRegion(page_index=page_image.page_index, rect_pt=rect_pt, label=label)
        for label, rect_pt in regions
    ]
    redact_pdf_regions(subset_pdf, redaction_regions)

    insert_results = insert_pdf_images(
        subset_pdf,
        [ImageInsertSpec(capture=capture) for capture in captures],
    )

    return subset_pdf, captures, insert_results


def _assert_regions_clean(pdf_path: Path, captures: Sequence[CapturedRegionImage]) -> None:
    doc = fitz.open(str(pdf_path))
    try:
        page = doc[0]
        for capture in captures:
            rect = fitz.Rect(*capture.rect_pt)
            assert _words_in_rect(page, rect) == []
    finally:
        doc.close()


def _assert_metadata(pdf_path: Path, insert_results: Sequence[ImageInsertResult]) -> None:
    doc = fitz.open(str(pdf_path))
    try:
        for result in insert_results:
            meta = doc.xref_get_key(result.image_xref, "/PDF2EPUBMetadata")
            assert meta is not None and meta[0] == "string"
            payload = json.loads(meta[1])
            assert payload["label"] in {"math", "table"}
            assert payload["width_px"] > 0 and payload["height_px"] > 0
    finally:
        doc.close()


@pytest.mark.skipif(not MASTERING_PDF.exists(), reason="Mastering.pdf no disponible")
@pytest.mark.skipif(not PADDLE.exists(), reason="paddleocr CLI no disponible en .venv")
def test_pdf_page_76_math_regions_are_unselectable(tmp_path: Path) -> None:
    pdf_path, captures, insert_results = _run_pipeline_for_page(tmp_path, 76)
    assert any(capture.label == "math" for capture in captures)
    assert len(insert_results) == len(captures)

    _assert_regions_clean(pdf_path, captures)
    _assert_metadata(pdf_path, insert_results)


@pytest.mark.skipif(not MASTERING_PDF.exists(), reason="Mastering.pdf no disponible")
@pytest.mark.skipif(not PADDLE.exists(), reason="paddleocr CLI no disponible en .venv")
def test_pdf_page_67_table_regions_are_unselectable(tmp_path: Path) -> None:
    pdf_path, captures, insert_results = _run_pipeline_for_page(tmp_path, 67)
    assert any(capture.label == "table" for capture in captures)
    assert len(insert_results) == len(captures)

    _assert_regions_clean(pdf_path, captures)
    _assert_metadata(pdf_path, insert_results)


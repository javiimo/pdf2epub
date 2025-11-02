from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence, Tuple

import pytest

fitz = pytest.importorskip("fitz")

from core.runner.detect_prep import prepare_page_images
from core.runner.layout import infer_layout_on_image
from core.runner.postprocess import PostprocessOptions, postprocess_math_and_tables
from core.runner.qa import draw_overlay
from core.runner.tables import fuse_tables_with_layout, infer_tables_on_image
from core.runner.coordinates import pixels_to_pdf_rect
from core.runner.region_capture import CaptureOptions, RegionSpec, capture_pdf_regions
from core.runner.pdf_insertion import ImageInsertOptions, ImageInsertSpec, insert_pdf_images


@dataclass(frozen=True)
class _Region:
    page_index: int
    rect_pt: Tuple[float, float, float, float]
    label: str


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _draw_pdf_regions(
    source_pdf: Path,
    destination_pdf: Path,
    regions: Sequence[_Region],
    *,
    fill: Tuple[float, float, float] | None = None,
    fill_opacity: float = 0.0,
    stroke: Tuple[float, float, float] = (1.0, 0.0, 0.0),
    stroke_width: float = 1.0,
) -> Path:
    doc = fitz.open(str(source_pdf))
    try:
        for region in regions:
            rect = fitz.Rect(*region.rect_pt)
            page = doc.load_page(region.page_index - 1)
            page.draw_rect(
                rect,
                color=stroke,
                fill=fill,
                width=stroke_width,
                fill_opacity=fill_opacity,
                overlay=True,
            )
        doc.save(str(destination_pdf))
    finally:
        doc.close()
    return destination_pdf


def test_full_preprocessing_pipeline_for_example_pdf():
    repo_root = Path(__file__).resolve().parents[1]
    pdf_path = repo_root / "example.pdf"
    assert pdf_path.exists(), "example.pdf no encontrado en la raíz del repositorio"

    temp_root = repo_root / "temp" / "example_preprocessing"
    if temp_root.exists():
        shutil.rmtree(temp_root)
    temp_root.mkdir(parents=True, exist_ok=True)

    paddleocr_path = repo_root / ".venv" / "bin" / "paddleocr"
    if not paddleocr_path.exists():
        pytest.skip("paddleocr no disponible en el entorno .venv")

    pages_dir = _ensure_dir(temp_root / "01-pages")
    page_images = prepare_page_images(pdf_path, pages_dir, dpi=360)
    assert len(page_images) == 2, "El PDF de ejemplo debería contener exactamente dos páginas"

    overlays_dir = _ensure_dir(temp_root / "02-overlays")
    crops_dir = _ensure_dir(temp_root / "03-captures")
    metadata_dir = _ensure_dir(temp_root / "04-metadata")

    highlight_pdf = temp_root / "05-highlighted.pdf"
    redacted_pdf = temp_root / "06-redacted.pdf"
    redacted_preview_pdf = temp_root / "07-redacted-preview.pdf"
    final_pdf = temp_root / "08-final.pdf"

    all_regions: list[_Region] = []
    captured_images: list[ImageInsertSpec] = []

    for page_image in page_images:
        page_index = page_image.page_index
        page_prefix = overlays_dir / f"page-{page_index:02d}"
        page_prefix.mkdir(parents=True, exist_ok=True)

        layout = infer_layout_on_image(
            page_image.image_path,
            device="cpu",
            paddleocr_path=str(paddleocr_path),
        )
        tables = infer_tables_on_image(
            page_image.image_path,
            device="cpu",
            paddleocr_path=str(paddleocr_path),
        )
        fused = fuse_tables_with_layout(layout, tables, iou_threshold=0.3)
        post = postprocess_math_and_tables(
            fused,
            options=PostprocessOptions(dpi=page_image.dpi, min_area_px=150, margin_pts=5.0),
        )
        boxes = [b for b in post.boxes if b.label in {"mathblock", "tableblock"}]

        overlay_path = page_prefix / "overlay.png"
        draw_overlay(page_image.image_path, boxes, overlay_path)
        assert overlay_path.exists() and overlay_path.stat().st_size > 0

        boxes_payload = [
            {
                "label": box.label,
                "score": box.score,
                "x": box.x,
                "y": box.y,
                "width": box.width,
                "height": box.height,
            }
            for box in boxes
        ]
        (page_prefix / "boxes.json").write_text(json.dumps(boxes_payload, indent=2), encoding="utf-8")

        pdf_rects = [
            pixels_to_pdf_rect(page_image, (box.x, box.y, box.width, box.height))
            for box in boxes
        ]
        (page_prefix / "boxes-pdf.json").write_text(
            json.dumps([
                {
                    "label": box.label,
                    "rect_pt": list(rect),
                }
                for box, rect in zip(boxes, pdf_rects)
            ], indent=2),
            encoding="utf-8",
        )

        page_regions = [_Region(page_index=page_index, rect_pt=rect, label=box.label) for box, rect in zip(boxes, pdf_rects)]
        all_regions.extend(page_regions)

        if page_regions:
            captures_dir = _ensure_dir(crops_dir / f"page-{page_index:02d}")
            effective_dpi = page_image.scale * 72.0
            captures = capture_pdf_regions(
                pdf_path,
                [RegionSpec(page_index=region.page_index, rect_pt=region.rect_pt, label=region.label) for region in page_regions],
                output_dir=captures_dir,
                options=CaptureOptions(dpi=effective_dpi, image_prefix=f"page{page_index:02d}"),
            )
            for capture in captures:
                spec = ImageInsertSpec(capture=capture)
                captured_images.append(spec)
                metadata_path = metadata_dir / f"capture-p{capture.page_index:02d}-{capture.image_path.stem}.json"
                metadata_path.write_text(
                    json.dumps({
                        "page_index": capture.page_index,
                        "rect_pt": list(capture.rect_pt),
                        "dpi": capture.dpi,
                        "label": capture.label,
                        "image": str(capture.image_path.relative_to(temp_root)),
                    }, indent=2),
                    encoding="utf-8",
                )

    _draw_pdf_regions(
        pdf_path,
        highlight_pdf,
        all_regions,
        fill=(1.0, 0.0, 0.0),
        fill_opacity=0.35,
        stroke=(1.0, 0.0, 0.0),
        stroke_width=0.5,
    )
    assert highlight_pdf.exists() and highlight_pdf.stat().st_size > 0

    if all_regions:
        _draw_pdf_regions(
            pdf_path,
            redacted_pdf,
            all_regions,
            fill=(1.0, 1.0, 1.0),
            fill_opacity=1.0,
            stroke=(1.0, 1.0, 1.0),
            stroke_width=0.0,
        )
    else:
        shutil.copy2(pdf_path, redacted_pdf)
    assert redacted_pdf.exists() and redacted_pdf.stat().st_size > 0

    _draw_pdf_regions(
        redacted_pdf,
        redacted_preview_pdf,
        all_regions,
        fill=None,
        fill_opacity=0.0,
        stroke=(1.0, 0.0, 0.0),
        stroke_width=1.0,
    )
    assert redacted_preview_pdf.exists() and redacted_preview_pdf.stat().st_size > 0

    if captured_images:
        insert_pdf_images(
            redacted_pdf,
            captured_images,
            output_pdf=final_pdf,
            options=ImageInsertOptions(paint_background=False, overlay=True),
        )
    else:
        shutil.copy2(redacted_pdf, final_pdf)
    assert final_pdf.exists() and final_pdf.stat().st_size > 0

    for capture_spec in captured_images:
        image_path = capture_spec.capture.image_path
        assert image_path.exists() and image_path.stat().st_size > 0


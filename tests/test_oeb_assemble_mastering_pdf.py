from pathlib import Path

import pytest

from core.configuration import TabConfiguration
from core.oeb.assemble import (
    FigureSpec,
    add_images_to_manifest,
    copy_images_into_oeb,
    insert_figures_into_html,
)
from core.runner.layout import infer_layout_on_image
from core.runner.postprocess import PostprocessOptions, postprocess_math_and_tables
from core.runner.preview import run_preview
from core.runner.rasterize import rasterize_boxes_from_pdf, rasterize_pdf_to_png
from core.runner.tables import fuse_tables_with_layout, infer_tables_on_image


MASTERING_PDF = Path(__file__).resolve().parents[1] / "Mastering.pdf"
PADDLE = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "paddleocr"


def _find_pdftocairo_png(prefix: Path, page: int) -> Path:
    # Handles both zero-padded and non-padded outputs
    candidates = [
        prefix.parent / f"{prefix.name}-{page}.png",
        prefix.parent / f"{prefix.name}-{page:03d}.png",
    ]
    for c in candidates:
        if c.exists():
            return c
    raise AssertionError(f"No se encontró la imagen rasterizada para la página {page} bajo {prefix.parent}")


@pytest.mark.skipif(not MASTERING_PDF.exists(), reason="Mastering.pdf no disponible")
@pytest.mark.skipif(not PADDLE.exists(), reason="paddleocr CLI no disponible en .venv")
def test_assemble_oeb_inserts_table_figures_page_67(tmp_path: Path):
    page = 67
    dpi = 360

    # 1) Rasterize page to PNG for detectors
    prefix = tmp_path / "p67"
    rasterize_pdf_to_png(MASTERING_PDF, prefix, dpi=dpi, first_page=page, last_page=page)
    png = _find_pdftocairo_png(prefix, page)

    # 2) Infer layout and tables and fuse, then postprocess to mathblock/tableblock
    layout = infer_layout_on_image(png, device="cpu", paddleocr_path=str(PADDLE))
    tables = infer_tables_on_image(png, device="cpu", paddleocr_path=str(PADDLE))
    fused = fuse_tables_with_layout(layout, tables, iou_threshold=0.3)
    post = postprocess_math_and_tables(fused, options=PostprocessOptions(min_area_px=150, dpi=dpi, margin_pts=5.0))

    # Ensure at least one tableblock exists on this page
    assert any(b.label == "tableblock" for b in post.boxes)

    # 3) Rasterize selective boxes from the PDF (not the PNG) to images
    out_dir = tmp_path / "selective-67"
    images = rasterize_boxes_from_pdf(MASTERING_PDF, out_dir, page, list(post.boxes), dpi=dpi)

    # 4) Generate OEB preview for only this page
    cfg = TabConfiguration(tab_id="t1", title="p67", input_pdf=MASTERING_PDF, page_range=f"{page}-{page}", options={})
    preview = run_preview(cfg)

    # 5) Copy images into OEB and update OPF manifest
    copied = copy_images_into_oeb(preview.oeb_output, list(images))
    add_images_to_manifest(preview.oeb_output / "content.opf", list(copied))

    # 6) Insert figures into the first spine HTML
    # Pair labels with images in order
    labels = [b.label for b in post.boxes if b.label in ("mathblock", "tableblock")]
    specs = [FigureSpec(image_path=img, label=lbl) for img, lbl in zip(copied, labels)]
    insert_figures_into_html(preview.spine_first_html, specs)

    # 7) Validate HTML contains at least one table figure and that images exist
    html = preview.spine_first_html.read_text(encoding="utf-8")
    assert "<figure class=\"table\">" in html or "class=\"tableblock\"" in html
    for p in copied:
        assert p.exists() and p.stat().st_size > 0


@pytest.mark.skipif(not MASTERING_PDF.exists(), reason="Mastering.pdf no disponible")
@pytest.mark.skipif(not PADDLE.exists(), reason="paddleocr CLI no disponible en .venv")
def test_assemble_oeb_inserts_math_figures_page_76(tmp_path: Path):
    page = 76
    dpi = 360

    # 1) Rasterize page to PNG for detectors
    prefix = tmp_path / "p76"
    rasterize_pdf_to_png(MASTERING_PDF, prefix, dpi=dpi, first_page=page, last_page=page)
    png = _find_pdftocairo_png(prefix, page)

    # 2) Infer layout (formulas expected) and postprocess
    layout = infer_layout_on_image(png, device="cpu", paddleocr_path=str(PADDLE))
    # No tables expected here, but infer and fuse defensively
    tables = infer_tables_on_image(png, device="cpu", paddleocr_path=str(PADDLE))
    fused = fuse_tables_with_layout(layout, tables, iou_threshold=0.3)
    post = postprocess_math_and_tables(fused, options=PostprocessOptions(min_area_px=150, dpi=dpi, margin_pts=5.0))

    # Ensure at least one mathblock exists on this page
    assert any(b.label == "mathblock" for b in post.boxes)

    # 3) Rasterize selective boxes
    out_dir = tmp_path / "selective-76"
    images = rasterize_boxes_from_pdf(MASTERING_PDF, out_dir, page, list(post.boxes), dpi=dpi)

    # 4) Generate OEB preview for only this page
    cfg = TabConfiguration(tab_id="t2", title="p76", input_pdf=MASTERING_PDF, page_range=f"{page}-{page}", options={})
    preview = run_preview(cfg)

    # 5) Copy images into OEB and update OPF
    copied = copy_images_into_oeb(preview.oeb_output, list(images))
    add_images_to_manifest(preview.oeb_output / "content.opf", list(copied))

    # 6) Insert figures
    labels = [b.label for b in post.boxes if b.label in ("mathblock", "tableblock")]
    specs = [FigureSpec(image_path=img, label=lbl) for img, lbl in zip(copied, labels)]
    insert_figures_into_html(preview.spine_first_html, specs)

    # 7) Validate HTML contains at least one mathblock img and that images exist
    html = preview.spine_first_html.read_text(encoding="utf-8")
    assert "class=\"mathblock\"" in html
    for p in copied:
        assert p.exists() and p.stat().st_size > 0


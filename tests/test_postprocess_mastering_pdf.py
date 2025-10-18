from pathlib import Path

import pytest

from core.runner.rasterize import rasterize_pdf_to_png
from core.runner.layout import infer_layout_on_image
from core.runner.tables import infer_tables_on_image, fuse_tables_with_layout
from core.runner.postprocess import postprocess_math_and_tables, PostprocessOptions


MASTERING_PDF = Path(__file__).resolve().parents[1] / "Mastering.pdf"
PADDLE = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "paddleocr"


def _find_png(prefix: Path, page: int) -> Path | None:
    # pdftocairo may or may not zero-pad page numbers
    for name in (f"{prefix.name}-{page}.png", f"{prefix.name}-{page:03d}.png"):
        candidate = prefix.parent / name
        if candidate.exists():
            return candidate
    return None


@pytest.mark.skipif(not MASTERING_PDF.exists(), reason="Mastering.pdf no disponible")
@pytest.mark.skipif(not PADDLE.exists(), reason="paddleocr CLI no disponible en .venv")
def test_postprocess_math_page_76(tmp_path: Path):
    prefix = tmp_path / "pp76"
    rasterize_pdf_to_png(MASTERING_PDF, prefix, dpi=360, first_page=76, last_page=76)
    png = _find_png(prefix, 76)
    assert png is not None and png.exists()

    layout = infer_layout_on_image(png, device="cpu", paddleocr_path=str(PADDLE))
    post = postprocess_math_and_tables(layout, options=PostprocessOptions(min_area_px=150, dpi=360, margin_pts=5.0))

    math_count = sum(1 for b in post.boxes if b.label == "mathblock")
    # Expect at least one math formula on page 76
    assert math_count >= 1


@pytest.mark.skipif(not MASTERING_PDF.exists(), reason="Mastering.pdf no disponible")
@pytest.mark.skipif(not PADDLE.exists(), reason="paddleocr CLI no disponible en .venv")
def test_postprocess_table_page_67(tmp_path: Path):
    prefix = tmp_path / "pp67"
    rasterize_pdf_to_png(MASTERING_PDF, prefix, dpi=360, first_page=67, last_page=67)
    png = _find_png(prefix, 67)
    assert png is not None and png.exists()

    layout = infer_layout_on_image(png, device="cpu", paddleocr_path=str(PADDLE))
    tables = infer_tables_on_image(png, device="cpu", paddleocr_path=str(PADDLE))
    fused = fuse_tables_with_layout(layout, tables, iou_threshold=0.3)
    post = postprocess_math_and_tables(fused, options=PostprocessOptions(min_area_px=150, dpi=360, margin_pts=5.0))

    table_count = sum(1 for b in post.boxes if b.label == "tableblock")
    # Expect at least one table on page 67
    assert table_count >= 1


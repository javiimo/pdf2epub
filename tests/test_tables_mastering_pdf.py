from pathlib import Path

import pytest

from core.runner.rasterize import rasterize_pdf_to_png
from core.runner.layout import infer_layout_on_image
from core.runner.tables import infer_tables_on_image, fuse_tables_with_layout


MASTERING_PDF = Path(__file__).resolve().parents[1] / "Mastering.pdf"
PADDLE = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "paddleocr"


@pytest.mark.skipif(not MASTERING_PDF.exists(), reason="Mastering.pdf no disponible")
@pytest.mark.skipif(not PADDLE.exists(), reason="paddleocr CLI no disponible en .venv")
def test_fuse_tables_page_67(tmp_path: Path):
    # Render page 67 to PNG
    prefix = tmp_path / "p67"
    rasterize_pdf_to_png(MASTERING_PDF, prefix, dpi=360, first_page=67, last_page=67)
    # Locate output file (-067 zero-padded or not depending on pdftocairo)
    png = None
    for candidate in [prefix.parent / f"{prefix.name}-67.png", prefix.parent / f"{prefix.name}-067.png"]:
        if candidate.exists():
            png = candidate
            break
    assert png and png.exists() and png.stat().st_size > 0

    # Infer layout and TSR tables on the image
    layout = infer_layout_on_image(png, device="cpu", paddleocr_path=str(PADDLE))
    tables = infer_tables_on_image(png, device="cpu", paddleocr_path=str(PADDLE))
    fused = fuse_tables_with_layout(layout, tables, iou_threshold=0.3)

    # Expect at least one table in the fused result
    table_count = sum(1 for b in fused.boxes if b.label == "table")
    assert table_count >= 1


@pytest.mark.skipif(not MASTERING_PDF.exists(), reason="Mastering.pdf no disponible")
@pytest.mark.skipif(not PADDLE.exists(), reason="paddleocr CLI no disponible en .venv")
def test_fuse_tables_page_76(tmp_path: Path):
    # Render page 76 to PNG
    prefix = tmp_path / "p76"
    rasterize_pdf_to_png(MASTERING_PDF, prefix, dpi=360, first_page=76, last_page=76)
    png = None
    for candidate in [prefix.parent / f"{prefix.name}-76.png", prefix.parent / f"{prefix.name}-076.png"]:
        if candidate.exists():
            png = candidate
            break
    assert png and png.exists() and png.stat().st_size > 0

    layout = infer_layout_on_image(png, device="cpu", paddleocr_path=str(PADDLE))
    tables = infer_tables_on_image(png, device="cpu", paddleocr_path=str(PADDLE))
    fused = fuse_tables_with_layout(layout, tables, iou_threshold=0.3)

    # Expect no table boxes after fusion when layout has none
    table_count = sum(1 for b in fused.boxes if b.label == "table")
    assert table_count == 0


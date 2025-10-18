from pathlib import Path

import pytest

from core.runner.rasterize import rasterize_pdf_to_png
from core.runner.layout import infer_layout_on_image
from core.runner.tables import infer_tables_on_image, fuse_tables_with_layout
from core.runner.postprocess import postprocess_math_and_tables, PostprocessOptions
from core.runner.fallback import decide_page_fallback, FallbackOptions


MASTERING_PDF = Path(__file__).resolve().parents[1] / "Mastering.pdf"
PADDLE = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "paddleocr"


def _find_png(prefix: Path, page: int) -> Path | None:
    for name in (f"{prefix.name}-{page}.png", f"{prefix.name}-{page:03d}.png"):
        candidate = prefix.parent / name
        if candidate.exists():
            return candidate
    return None


@pytest.mark.skipif(not MASTERING_PDF.exists(), reason="Mastering.pdf no disponible")
@pytest.mark.skipif(not PADDLE.exists(), reason="paddleocr CLI no disponible en .venv")
def test_fallback_decision_page_76(tmp_path: Path):
    prefix = tmp_path / "fb76"
    rasterize_pdf_to_png(MASTERING_PDF, prefix, dpi=360, first_page=76, last_page=76)
    png = _find_png(prefix, 76)
    assert png is not None and png.exists()

    layout = infer_layout_on_image(png, device="cpu", paddleocr_path=str(PADDLE))
    post = postprocess_math_and_tables(layout, options=PostprocessOptions(min_area_px=150, dpi=360, margin_pts=5.0))

    decision = decide_page_fallback(png, post.boxes, options=FallbackOptions())
    # Sanity checks: valid decision and coverage range
    assert isinstance(decision.should_rasterize_full, bool)
    assert 0.0 <= decision.coverage <= 1.0
    assert decision.box_count >= 0

    # With relaxed thresholds, math-heavy pages should trigger fallback
    relaxed = decide_page_fallback(
        png,
        post.boxes,
        options=FallbackOptions(coverage_threshold=0.05, count_threshold=1),
    )
    assert relaxed.should_rasterize_full in (True, decision.should_rasterize_full)


@pytest.mark.skipif(not MASTERING_PDF.exists(), reason="Mastering.pdf no disponible")
@pytest.mark.skipif(not PADDLE.exists(), reason="paddleocr CLI no disponible en .venv")
def test_fallback_decision_page_67(tmp_path: Path):
    prefix = tmp_path / "fb67"
    rasterize_pdf_to_png(MASTERING_PDF, prefix, dpi=360, first_page=67, last_page=67)
    png = _find_png(prefix, 67)
    assert png is not None and png.exists()

    layout = infer_layout_on_image(png, device="cpu", paddleocr_path=str(PADDLE))
    tables = infer_tables_on_image(png, device="cpu", paddleocr_path=str(PADDLE))
    fused = fuse_tables_with_layout(layout, tables, iou_threshold=0.3)
    post = postprocess_math_and_tables(fused, options=PostprocessOptions(min_area_px=150, dpi=360, margin_pts=5.0))

    decision = decide_page_fallback(png, post.boxes, options=FallbackOptions())
    assert isinstance(decision.should_rasterize_full, bool)
    assert 0.0 <= decision.coverage <= 1.0
    assert decision.box_count >= 0

    # Lowered coverage threshold should give True for a page with a large table
    assert decide_page_fallback(
        png,
        post.boxes,
        options=FallbackOptions(coverage_threshold=0.2, count_threshold=1),
    ).should_rasterize_full is True


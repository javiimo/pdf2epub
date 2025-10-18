from pathlib import Path
import os

import pytest

from core.runner.rasterize import rasterize_pdf_to_png


MASTERING_PDF = Path(__file__).resolve().parents[1] / "Mastering.pdf"


@pytest.mark.skipif(not MASTERING_PDF.exists(), reason="Mastering.pdf no disponible")
def test_mastering_page_76_renders_png(tmp_path: Path):
    prefix = tmp_path / "mastering-76"
    rasterize_pdf_to_png(
        MASTERING_PDF,
        prefix,
        dpi=360,
        first_page=76,
        last_page=76,
    )
    # pdftocairo zero-pads page numbers (e.g., -076)
    candidates = [
        prefix.parent / f"{prefix.name}-76.png",
        prefix.parent / f"{prefix.name}-076.png",
    ]
    assert any(p.exists() and p.stat().st_size > 0 for p in candidates)


@pytest.mark.skipif(not MASTERING_PDF.exists(), reason="Mastering.pdf no disponible")
def test_mastering_page_67_renders_png(tmp_path: Path):
    prefix = tmp_path / "mastering-67"
    rasterize_pdf_to_png(
        MASTERING_PDF,
        prefix,
        dpi=360,
        first_page=67,
        last_page=67,
    )
    candidates = [
        prefix.parent / f"{prefix.name}-67.png",
        prefix.parent / f"{prefix.name}-067.png",
    ]
    assert any(p.exists() and p.stat().st_size > 0 for p in candidates)

from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

from core.configuration import TabConfiguration
from core.runner.enrich import EnrichOptions, enrich_oeb_with_ml
from core.runner.epub import package_epub_from_oeb
from core.runner.preview import run_preview


MASTERING_PDF = Path(__file__).resolve().parents[1] / "Mastering.pdf"
PADDLE = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "paddleocr"


def _zip_read_texts(epub_path: Path) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    with zipfile.ZipFile(epub_path, "r") as zf:
        for name in zf.namelist():
            if name.lower().endswith((".xhtml", ".html")):
                try:
                    data = zf.read(name)
                    items.append((name, data.decode("utf-8", errors="ignore")))
                except KeyError:
                    continue
    return items


def _ci(s: str) -> str:
    return (s or "").lower()


@pytest.mark.skipif(not MASTERING_PDF.exists(), reason="Mastering.pdf no disponible")
@pytest.mark.skipif(not PADDLE.exists(), reason="paddleocr CLI no disponible en .venv")
def test_package_epub_preserves_table_on_p67(tmp_path: Path):
    # preview → enrich → package
    cfg = TabConfiguration(tab_id="p67", title="p67", input_pdf=MASTERING_PDF, page_range="67-67", options={})
    preview = run_preview(cfg)
    enrich_oeb_with_ml(preview, options=EnrichOptions(dpi=360, device="cpu", paddleocr_path=str(PADDLE)), run=subprocess.run)

    target = tmp_path / "out" / "p67.epub"
    res = package_epub_from_oeb(cfg, preview.oeb_output, target, run=subprocess.run)
    assert res.target.exists() and res.target.stat().st_size > 0

    # Inspect EPUB HTMLs
    entries = _zip_read_texts(target)
    assert entries, "No se encontraron entradas HTML en el EPUB"
    found = False
    for name, text in entries:
        if "class=\"tableblock\"" in text:
            found = True
            low = _ci(text)
            a = low.find("worked examples")
            rbounds = [
                "table 2.3: examples of entropy rate and resulting",
                "table 2.3",
            ]
            b = -1
            for r in rbounds:
                b = low.find(r)
                if b != -1:
                    break
            if a != -1 and b != -1 and a < b:
                idx = low.find("class=\"tableblock\"")
                assert a <= idx <= b
            break
    assert found, "No se encontró ninguna figura de tabla en el EPUB"


@pytest.mark.skipif(not MASTERING_PDF.exists(), reason="Mastering.pdf no disponible")
@pytest.mark.skipif(not PADDLE.exists(), reason="paddleocr CLI no disponible en .venv")
def test_package_epub_preserves_math_on_p76(tmp_path: Path):
    cfg = TabConfiguration(tab_id="p76", title="p76", input_pdf=MASTERING_PDF, page_range="76-76", options={})
    preview = run_preview(cfg)
    enrich_oeb_with_ml(preview, options=EnrichOptions(dpi=360, device="cpu", paddleocr_path=str(PADDLE)), run=subprocess.run)

    target = tmp_path / "out" / "p76.epub"
    res = package_epub_from_oeb(cfg, preview.oeb_output, target, run=subprocess.run)
    assert res.target.exists() and res.target.stat().st_size > 0

    entries = _zip_read_texts(target)
    assert entries, "No se encontraron entradas HTML en el EPUB"
    # At least one math figure exists, and if clue pairs appear ensure one sits between
    any_math = False
    for name, text in entries:
        low = _ci(text)
        if "class=\"mathblock\"" in low:
            any_math = True
            # Validate against provided pairs opportunistically
            pairs = [
                ("chapter 2. the forecastability of time series: understanding the limits", "pe evaluates"),
                ("construct a window:", "rank the m values"),
                (" probability distribution", "4. compute shannon entropy:"),
                ("4. compute shannon entropy:", "(log base 2 is often"),
                ("permutation entropy:", "interpretation of extreme cases"),
            ]
            for left, right in pairs:
                li = low.find(left)
                ri = low.find(right)
                if li != -1 and ri != -1 and li < ri:
                    mid = low.find("class=\"mathblock\"", li, ri)
                    assert mid != -1
            break
    assert any_math, "No se encontró ninguna figura mathblock en el EPUB"


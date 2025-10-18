from pathlib import Path

import pytest

from core.configuration import TabConfiguration
from core.oeb.assemble import install_default_css
from core.runner.preview import run_preview


MASTERING_PDF = Path(__file__).resolve().parents[1] / "Mastering.pdf"


@pytest.mark.skipif(not MASTERING_PDF.exists(), reason="Mastering.pdf no disponible")
@pytest.mark.parametrize("page", [67, 76])
def test_install_default_css_into_oeb(tmp_path: Path, page: int):
    # Generate OEB preview for the single page
    cfg = TabConfiguration(
        tab_id=f"t{page}", title=f"p{page}", input_pdf=MASTERING_PDF, page_range=f"{page}-{page}", options={}
    )
    preview = run_preview(cfg)

    # Install default CSS and ensure it is present and linked
    css_path = install_default_css(preview.oeb_output)
    assert css_path.exists()
    css_text = css_path.read_text(encoding="utf-8")
    assert ".mathblock" in css_text
    assert ".table" in css_text
    assert "page-break-inside:avoid" in css_text

    html_text = preview.spine_first_html.read_text(encoding="utf-8")
    assert "pdf2epub.css" in html_text


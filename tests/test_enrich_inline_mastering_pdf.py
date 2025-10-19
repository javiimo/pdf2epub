from pathlib import Path

import pytest

from core.configuration import TabConfiguration
from core.runner.enrich import enrich_oeb_with_ml, EnrichOptions
from core.runner.preview import run_preview


MASTERING_PDF = Path(__file__).resolve().parents[1] / "Mastering.pdf"
PADDLE = Path(__file__).resolve().parents[1] / ".venv" / "bin" / "paddleocr"


def _ci_normalize(s: str) -> str:
    return (s or "").lower()


def _segment_bounds(html_text: str, segment_index: int) -> tuple[int, int]:
    """Return start,end indices for the Nth page segment based on pagebreaks.

    Segments are determined like core.oeb.assemble._find_pagebreaks: split the
    HTML around anchors like calibre_pb_*, epub:type=pagebreak, or hr.pagebreak.
    """
    import re

    text = html_text
    patterns = [
        r"<a[^>]+(?:id|name)=(?:\"|')calibre_pb_\d+(?:\"|')[^>]*>\s*</a>",
        r"<[^>]+epub:type=(?:\"|')pagebreak(?:\"|')[^>]*>",
        r"<hr[^>]*class=(?:\"|')[^\"']*pagebreak[^\"']*(?:\"|')[^>]*>",
        r"<span[^>]*class=(?:\"|')[^\"']*pagebreak[^\"']*(?:\"|')[^>]*>\s*</span>",
    ]
    breaks: list[int] = []
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE | re.DOTALL):
            breaks.append(m.end())
    breaks.sort()
    segs: list[tuple[int, int]] = []
    if not breaks:
        segs = [(0, len(text))]
    else:
        start = 0
        for b in breaks:
            segs.append((start, b))
            start = b
        segs.append((start, len(text)))
    if segment_index < 0 or segment_index >= len(segs):
        raise AssertionError(f"Segmento fuera de rango: {segment_index} de {len(segs)}")
    return segs[segment_index]


@pytest.mark.skipif(not MASTERING_PDF.exists(), reason="Mastering.pdf no disponible")
@pytest.mark.skipif(not PADDLE.exists(), reason="paddleocr CLI no disponible en .venv")
def test_enrich_inserts_table_on_page_67(tmp_path: Path):
    cfg = TabConfiguration(
        tab_id="t-p67",
        title="p67",
        input_pdf=MASTERING_PDF,
        page_range="67-67",
        options={},
    )

    import subprocess

    preview = run_preview(cfg)
    enrich_oeb_with_ml(
        preview,
        options=EnrichOptions(dpi=360, device="cpu", paddleocr_path=str(PADDLE)),
        run=subprocess.run,
    )

    html = preview.spine_first_html.read_text(encoding="utf-8", errors="ignore")
    start, end = _segment_bounds(html, 0)
    segment = html[start:end]

    # Expect at least one table figure in this page's segment
    assert segment.count("class=\"tableblock\"") >= 1

    # If anchor texts are present, ensure figure sits between them
    low = _ci_normalize(segment)
    a = low.find("worked examples")
    # Prefer full title if present, fallback to short "table 2.3"
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
        fig_idx = low.find("class=\"tableblock\"")
        assert a <= fig_idx <= b


@pytest.mark.skipif(not MASTERING_PDF.exists(), reason="Mastering.pdf no disponible")
@pytest.mark.skipif(not PADDLE.exists(), reason="paddleocr CLI no disponible en .venv")
def test_enrich_inserts_math_on_page_67_between_bounds(tmp_path: Path):
    cfg = TabConfiguration(
        tab_id="t-p67-math",
        title="p67-math",
        input_pdf=MASTERING_PDF,
        page_range="67-67",
        options={},
    )

    import subprocess

    preview = run_preview(cfg)
    enrich_oeb_with_ml(
        preview,
        options=EnrichOptions(dpi=360, device="cpu", paddleocr_path=str(PADDLE)),
        run=subprocess.run,
    )

    html = preview.spine_first_html.read_text(encoding="utf-8", errors="ignore")
    start, end = _segment_bounds(html, 0)
    segment = html[start:end]

    # Expect at least one math figure in this page's segment
    assert segment.count("class=\"mathblock\"") >= 1

    low = _ci_normalize(segment)
    left = low.find("is bounded by:")
    right = low.find("as entropy increases toward its maximum")
    if left != -1 and right != -1 and left < right:
        mid = low.find("class=\"mathblock\"", left, right)
        assert mid != -1


@pytest.mark.skipif(not MASTERING_PDF.exists(), reason="Mastering.pdf no disponible")
@pytest.mark.skipif(not PADDLE.exists(), reason="paddleocr CLI no disponible en .venv")
def test_enrich_inserts_math_on_page_76_between_clues(tmp_path: Path):
    cfg = TabConfiguration(
        tab_id="t-p76",
        title="p76",
        input_pdf=MASTERING_PDF,
        page_range="76-76",
        options={},
    )

    import subprocess

    preview = run_preview(cfg)
    enrich_oeb_with_ml(
        preview,
        options=EnrichOptions(dpi=360, device="cpu", paddleocr_path=str(PADDLE)),
        run=subprocess.run,
    )

    html = preview.spine_first_html.read_text(encoding="utf-8", errors="ignore")
    start, end = _segment_bounds(html, 0)
    segment = html[start:end]
    low = _ci_normalize(segment)

    # We expect at least one math figure on this page
    assert segment.count("class=\"mathblock\"") >= 1

    # For each provided clue pair, if both appear, assert a math figure exists between them
    clue_pairs = [
        ("chapter 2. the forecastability of time series: understanding the limits", "pe evaluates"),
        ("construct a window:", "rank the m values"),
        ("probability distribution", "4. compute shannon entropy:"),
        ("4. compute shannon entropy:", "(log base 2 is often"),
        ("permutation entropy:", "interpretation of extreme cases"),
    ]

    for left, right in clue_pairs:
        li = low.find(left)
        ri = low.find(right)
        if li != -1 and ri != -1 and li < ri:
            mid = low.find("class=\"mathblock\"", li, ri)
            assert mid != -1

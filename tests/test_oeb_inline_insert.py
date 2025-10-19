from pathlib import Path

from core.oeb.assemble import FigureSpec, insert_figures_inline


def _write_html(tmp_path: Path, body: str) -> Path:
    html = f"""
    <html>
    <head><title>T</title></head>
    <body>
    {body}
    </body>
    </html>
    """
    p = tmp_path / "doc.xhtml"
    p.write_text(html, encoding="utf-8")
    return p


def test_insert_inline_uses_pagebreak_segments(tmp_path: Path):
    # Three pages: [p1], [p2], [p3]
    body = (
        "<p>p1-a</p><p>p1-b</p>"
        "<a id=\"calibre_pb_1\"></a>"
        "<p>p2-a</p><p>p2-b</p><p>p2-c</p>"
        "<a id=\"calibre_pb_2\"></a>"
        "<p>p3-a</p>"
    )
    html = _write_html(tmp_path, body)

    # Create two figures for page 2 with different vertical ratios
    img1 = tmp_path / "img1.png"
    img2 = tmp_path / "img2.png"
    img1.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x10\x00\x00\x00\x10\x08\x02\x00\x00\x00\x90wS\xde")
    img2.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x10\x00\x00\x00\x10\x08\x02\x00\x00\x00\x90wS\xde")

    # page_height arbitrary; y defines ratio y/page_height
    f1 = FigureSpec(image_path=img1, label="mathblock", page_index=2, y=10, page_height=100)
    f2 = FigureSpec(image_path=img2, label="tableblock", page_index=2, y=90, page_height=100)

    insert_figures_inline(html, [f1, f2], page_offset=1, remove_math_text=False)

    out = html.read_text(encoding="utf-8")
    # Both figures must sit between the markers of page 2
    start = out.find("calibre_pb_1")
    end = out.find("calibre_pb_2")
    assert start != -1 and end != -1 and start < end
    segment = out[start:end]
    assert segment.count("class=\"mathblock\"") == 1
    assert segment.count("class=\"tableblock\"") == 1
    # Ensure ordering respects vertical ratio: mathblock (y=10) before tableblock (y=90)
    mi = segment.find('class="mathblock"')
    ti = segment.find('class="tableblock"')
    assert -1 not in (mi, ti)
    assert mi < ti


def test_insert_inline_removes_math_like_paragraph(tmp_path: Path):
    # Three segments with explicit pagebreaks; middle segment contains math-like paragraph
    body = (
        "<p>Before</p>"
        "<a id=\"calibre_pb_1\"></a>"
        "<p>E = mc^2</p>"
        "<a id=\"calibre_pb_2\"></a>"
        "<p>After</p>"
    )
    html = _write_html(tmp_path, body)

    img = tmp_path / "img.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x10\x00\x00\x00\x10\x08\x02\x00\x00\x00\x90wS\xde")
    # Place figure into segment 2 so cleanup affects only the math paragraph
    f = FigureSpec(image_path=img, label="mathblock", page_index=2, y=50, page_height=100)
    insert_figures_inline(html, [f], page_offset=1, remove_math_text=True)

    out = html.read_text(encoding="utf-8")
    assert "E = mc^2" not in out
    # A normal paragraph remains intact
    assert "After" in out

from pathlib import Path

import pytest

from core.parser.opf import OpfParserError, find_first_spine_html, parse_spine


OPF_TEMPLATE = """<?xml version='1.0' encoding='utf-8'?>
<package xmlns='http://www.idpf.org/2007/opf'>
  <manifest>
    <item id='cover' href='Text/cover.xhtml' media-type='application/xhtml+xml'/>
    <item id='chap1' href='Text/chapter1.xhtml' media-type='application/xhtml+xml'/>
    <item id='notes' href='Text/notes.xhtml' media-type='application/xhtml+xml'/>
  </manifest>
  <spine>
    <itemref idref='cover' linear='no'/>
    <itemref idref='chap1'/>
    <itemref idref='notes'/>
  </spine>
</package>
"""


NO_NAMESPACE_OPF = """<?xml version='1.0' encoding='utf-8'?>
<package>
  <manifest>
    <item id='chap1' href='chapter1.xhtml' media-type='text/html'/>
  </manifest>
  <spine>
    <itemref idref='chap1'/>
  </spine>
</package>
"""


def _prepare_oeb(tmp_path: Path, contents: str) -> Path:
    oeb = tmp_path / "oeb"
    (oeb / "Text").mkdir(parents=True)
    (oeb / "Text" / "chapter1.xhtml").write_text("<html/>", encoding="utf-8")
    (oeb / "Text" / "notes.xhtml").write_text("<html/>", encoding="utf-8")
    (oeb / "Text" / "cover.xhtml").write_text("<html/>", encoding="utf-8")
    (oeb / "content.opf").write_text(contents, encoding="utf-8")
    return oeb


def test_find_first_spine_html_skips_non_linear(tmp_path):
    oeb = _prepare_oeb(tmp_path, OPF_TEMPLATE)
    first = find_first_spine_html(oeb)
    assert first.name == "chapter1.xhtml"


def test_parse_spine_without_namespace(tmp_path):
    oeb = tmp_path / "plain"
    oeb.mkdir()
    (oeb / "chapter1.xhtml").write_text("<html/>", encoding="utf-8")
    (oeb / "content.opf").write_text(NO_NAMESPACE_OPF, encoding="utf-8")

    entries = parse_spine(oeb)
    assert len(entries) == 1
    assert entries[0].href.name == "chapter1.xhtml"
    assert entries[0].linear is True


def test_find_first_spine_html_errors_when_missing(tmp_path):
    oeb = tmp_path / "missing"
    oeb.mkdir()
    with pytest.raises(OpfParserError):
        find_first_spine_html(oeb)

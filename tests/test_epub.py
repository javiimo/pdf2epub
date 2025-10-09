import subprocess
from pathlib import Path
from typing import List

import pytest

from core.configuration import TabConfiguration
from core.options.catalog import get_catalog
from core.runner.epub import ConversionError, run_epub


def _make_config(tmp_path: Path) -> TabConfiguration:
    input_pdf = tmp_path / "input.pdf"
    input_pdf.write_bytes(b"%PDF-1.4")
    return TabConfiguration(tab_id="t1", title="Test", input_pdf=input_pdf, options={})


def test_run_epub_creates_output(tmp_path):
    config = _make_config(tmp_path)
    target = tmp_path / "output" / "book.epub"
    workspace_dir = tmp_path / "workspace"

    class DummyWorkspace:
        def __init__(self, path):
            self.path = path
            self.path.mkdir()
            self.cleaned = False

        def cleanup(self):
            self.cleaned = True

    def workspace_factory():
        return DummyWorkspace(workspace_dir)

    def fake_run(command, capture_output, text, check):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"epub")
        return subprocess.CompletedProcess(command, returncode=0, stdout="ok", stderr="warn")

    result = run_epub(
        config,
        target=target,
        catalog=get_catalog(),
        workspace_factory=workspace_factory,
        run=fake_run,
    )

    assert result.target == target
    assert target.exists()
    assert result.stdout == "ok"
    assert result.stderr == "warn"


def test_run_epub_cleans_workspace_on_failure(tmp_path):
    config = _make_config(tmp_path)
    target = tmp_path / "salida.epub"
    cleanup: List[str] = []

    class DummyWorkspace:
        def __init__(self, path):
            self.path = path
            self.path.mkdir()

        def cleanup(self):
            cleanup.append("done")

    def workspace_factory():
        return DummyWorkspace(tmp_path / "ws")

    def fake_run(command, capture_output, text, check):
        return subprocess.CompletedProcess(command, returncode=2, stdout="out", stderr="err")

    with pytest.raises(ConversionError) as excinfo:
        run_epub(
            config,
            target=target,
            catalog=get_catalog(),
            workspace_factory=workspace_factory,
            run=fake_run,
        )

    assert excinfo.value.returncode == 2
    assert cleanup == ["done"]


def test_run_epub_requires_input(tmp_path):
    config = TabConfiguration(tab_id="t1", title="Sin PDF")

    with pytest.raises(ConversionError):
        run_epub(config, target=tmp_path / "book.epub", catalog=get_catalog())


def test_run_epub_wraps_subset_error(tmp_path, monkeypatch):
    from core.runner.pdf_subset import PdfSubsetError

    config = _make_config(tmp_path)
    target = tmp_path / "salida.epub"

    cleanup_flag = {"called": False}

    class DummyWorkspace:
        def __init__(self, path):
            self.path = path
            self.path.mkdir()

        def cleanup(self):
            cleanup_flag["called"] = True

    def workspace_factory():
        return DummyWorkspace(tmp_path / "ws")

    from core.runner import epub as epub_module

    def fake_prepare(*args, **kwargs):
        raise PdfSubsetError("subset fail")

    monkeypatch.setattr(epub_module, "prepare_pdf_subset", fake_prepare)

    with pytest.raises(ConversionError) as excinfo:
        run_epub(config, target=target, workspace_factory=workspace_factory)

    assert "subset fail" in str(excinfo.value)
    assert cleanup_flag["called"] is True

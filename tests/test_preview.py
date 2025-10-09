import subprocess
from typing import List

import pytest

from core.configuration import TabConfiguration
from core.options.catalog import get_catalog
from core.runner.preview import PreviewError, run_preview
from core.runner.pdf_subset import PdfSubsetError


def _make_config(tmp_path) -> TabConfiguration:
    input_pdf = tmp_path / "input.pdf"
    input_pdf.write_bytes(b"%PDF-1.4")
    return TabConfiguration(tab_id="t1", title="Test", input_pdf=input_pdf, options={})


def test_run_preview_returns_result(tmp_path):
    config = _make_config(tmp_path)
    workspace_path = tmp_path / "workspace"

    class DummyWorkspace:
        def __init__(self, path):
            self.path = path
            self.cleaned = False
            self.path.mkdir()

        def cleanup(self):
            self.cleaned = True

    def workspace_factory():
        return DummyWorkspace(workspace_path)

    def fake_run(command, capture_output, text, check):
        (workspace_path / "preview-oeb").mkdir()
        return subprocess.CompletedProcess(command, returncode=0, stdout="ok", stderr="warn")

    config.options["base-font-size"] = "13"

    result = run_preview(
        config,
        catalog=get_catalog(),
        workspace_factory=workspace_factory,
        run=fake_run,
    )

    assert result.oeb_output == workspace_path / "preview-oeb"
    assert result.stdout == "ok"
    assert result.stderr == "warn"
    assert result.workspace.cleaned is False


def test_run_preview_cleans_workspace_on_failure(tmp_path):
    config = _make_config(tmp_path)
    workspace_path = tmp_path / "workspace-fail"
    cleanup_calls: List[str] = []

    class DummyWorkspace:
        def __init__(self, path):
            self.path = path
            self.path.mkdir()

        def cleanup(self):
            cleanup_calls.append("cleaned")

    def workspace_factory():
        return DummyWorkspace(workspace_path)

    def fake_run(command, capture_output, text, check):
        return subprocess.CompletedProcess(command, returncode=2, stdout="out", stderr="err")

    with pytest.raises(PreviewError) as excinfo:
        run_preview(
            config,
            catalog=get_catalog(),
            workspace_factory=workspace_factory,
            run=fake_run,
        )

    assert excinfo.value.returncode == 2
    assert cleanup_calls == ["cleaned"]


def test_run_preview_requires_input_pdf(tmp_path):
    config = TabConfiguration(tab_id="t1", title="Test")

    with pytest.raises(PreviewError):
        run_preview(config, catalog=get_catalog())


def test_run_preview_wraps_subset_errors(tmp_path, monkeypatch):
    config = _make_config(tmp_path)
    workspace_path = tmp_path / "ws-subset"
    cleanup_flag = {"called": False}

    class DummyWorkspace:
        def __init__(self, path):
            self.path = path
            self.path.mkdir()

        def cleanup(self):
            cleanup_flag["called"] = True

    def workspace_factory():
        return DummyWorkspace(workspace_path)

    def fake_prepare(*_, **__):
        raise PdfSubsetError("subset error")

    monkeypatch.setattr("core.runner.preview.prepare_pdf_subset", fake_prepare)

    with pytest.raises(PreviewError) as excinfo:
        run_preview(config, catalog=get_catalog(), workspace_factory=workspace_factory)

    assert "subset error" in str(excinfo.value)
    assert cleanup_flag["called"] is True

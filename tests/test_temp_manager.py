import importlib
import os
import sys
from pathlib import Path

import pytest


def _reload_with_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    module_name = "core.runner.temp_manager"
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    # Ensure tempfile picks up new TMPDIR by clearing cached value.
    import tempfile

    tempfile.tempdir = None
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def test_cleanup_stale_directories_on_import(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    stale = tmp_path / "pdf2epub-old"
    stale.mkdir()
    untouched = tmp_path / "other-prefix"
    untouched.mkdir()

    module = _reload_with_tmp(monkeypatch, tmp_path)

    assert not stale.exists()
    assert untouched.exists()
    assert module.TEMP_PREFIX == "pdf2epub-"


def test_temporary_workspace_creates_and_cleans(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    module = _reload_with_tmp(monkeypatch, tmp_path)

    workspace = module.TemporaryWorkspace()
    try:
        assert workspace.path.exists()
        assert workspace.path.name.startswith(module.TEMP_PREFIX)
    finally:
        workspace.cleanup()

    assert not workspace.path.exists()


def test_cleanup_all_removes_active_workspaces(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    module = _reload_with_tmp(monkeypatch, tmp_path)

    ws1 = module.TemporaryWorkspace()
    ws2 = module.TemporaryWorkspace()
    assert ws1.path.exists()
    assert ws2.path.exists()

    module.cleanup_all()

    assert not ws1.path.exists()
    assert not ws2.path.exists()

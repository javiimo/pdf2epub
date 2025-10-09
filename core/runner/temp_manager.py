"""Temporary workspace helpers for pdf2epub conversion flows.

This module centralises creation and cleanup of temporary directories used
across the application. It guarantees that directories created with the
``pdf2epub-`` prefix are removed on process exit and clears any stale
directories left behind from previous runs when the module is imported.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from pathlib import Path
from threading import Lock
from typing import List, Optional

TEMP_PREFIX = "pdf2epub-"

_LOCK = Lock()
_ACTIVE: List["TemporaryWorkspace"] = []


class TemporaryWorkspaceError(RuntimeError):
    """Raised when creating or cleaning up a temporary workspace fails."""


def _default_temp_root() -> Path:
    return Path(tempfile.gettempdir())


def cleanup_stale(prefix: str = TEMP_PREFIX, temp_root: Optional[Path] = None) -> None:
    """Remove leftover directories from previous runs.

    Args:
        prefix: Directory name prefix to search for.
        temp_root: Directory to inspect; defaults to the platform temp dir.
    """
    root = Path(temp_root) if temp_root is not None else _default_temp_root()

    if not root.exists():
        return

    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith(prefix):
            shutil.rmtree(entry, ignore_errors=True)


class TemporaryWorkspace:
    """Managed temporary directory with automatic cleanup."""

    def __init__(self, *, prefix: str = TEMP_PREFIX) -> None:
        try:
            self._tempdir = tempfile.TemporaryDirectory(prefix=prefix)
        except OSError as exc:
            raise TemporaryWorkspaceError(
                f"No se pudo crear el directorio temporal '{prefix}*': {exc}"
            ) from exc
        self._path = Path(self._tempdir.name)
        self._prefix = prefix
        self._closed = False
        with _LOCK:
            _ACTIVE.append(self)

    @property
    def path(self) -> Path:
        """Absolute path to the managed directory."""
        return self._path

    def cleanup(self) -> None:
        """Remove the underlying temporary directory."""
        if self._closed:
            return
        try:
            self._tempdir.cleanup()
        except OSError as exc:
            raise TemporaryWorkspaceError(
                f"No se pudo limpiar el directorio temporal '{self._path}': {exc}"
            ) from exc
        finally:
            self._closed = True
            with _LOCK:
                if self in _ACTIVE:
                    _ACTIVE.remove(self)

    def __enter__(self) -> "TemporaryWorkspace":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.cleanup()

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return f"TemporaryWorkspace(path={self._path!s})"


def cleanup_all() -> None:
    """Cleanup every tracked workspace, used at process shutdown."""
    with _LOCK:
        pending = list(_ACTIVE)
    for workspace in pending:
        workspace.cleanup()


def _register_atexit_cleanup() -> None:
    atexit.register(cleanup_all)


cleanup_stale()
_register_atexit_cleanup()

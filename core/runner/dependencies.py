"""Helpers to verify required external binaries."""

from __future__ import annotations

import shutil
from typing import Callable, Dict, Optional


class MissingDependencyError(RuntimeError):
    """Raised when a required binary is not available in PATH."""


REQUIRED_BINARIES: Dict[str, str] = {
    "ebook-convert": "Calibre CLI para conversiones PDF->EPUB",
    "qpdf": "Utilidad CLI para preprocesar PDF (subconjuntos de paginas)",
}


AlertCallback = Callable[[str, Dict[str, str]], None]


def _format_missing_message(missing: Dict[str, str]) -> str:
    entries = ", ".join(f"{name} ({details})" for name, details in missing.items())
    return (
        "No se encontraron dependencias obligatorias en PATH: "
        f"{entries}. Ajusta tu instalacion y reintenta."
    )


def verify_required_binaries(
    alert_callback: Optional[AlertCallback] = None,
    which: Callable[[str], Optional[str]] = shutil.which,
) -> Dict[str, str]:
    """Ensure the required external tools are available in PATH.

    Args:
        alert_callback: Optional callable invoked when binaries are missing.
                        Receives the user-facing message and the mapping of
                        missing binary → descripción.
        which: Resolver compatible con shutil.which para facilitar pruebas.

    Returns:
        Mapping binary name → resolved absolute path.

    Raises:
        MissingDependencyError: When one or more required binaries are absent.
    """
    resolved: Dict[str, str] = {}
    missing: Dict[str, str] = {}

    for binary, details in REQUIRED_BINARIES.items():
        path = which(binary)
        if path:
            resolved[binary] = path
        else:
            missing[binary] = details

    if missing:
        message = _format_missing_message(missing)
        if alert_callback is not None:
            alert_callback(message, missing)
        raise MissingDependencyError(message)

    return resolved

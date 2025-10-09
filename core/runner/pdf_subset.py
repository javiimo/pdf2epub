"""Helpers to generate page-range subsets using qpdf."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Optional, Sequence

__all__ = ["PdfSubsetError", "prepare_pdf_subset"]


class PdfSubsetError(RuntimeError):
    """Raised when generating a subset PDF fails."""


def _ensure_input_path(path: Optional[Path]) -> Path:
    if path is None:
        raise PdfSubsetError("Se requiere un archivo PDF de entrada para aplicar un rango de páginas.")
    return Path(path)


def _build_output_path(workspace: Path, input_pdf: Path) -> Path:
    stem = input_pdf.stem or "pdf"
    return workspace / f"{stem}-subset.pdf"


def prepare_pdf_subset(
    input_pdf: Optional[Path],
    *,
    workspace: Path,
    page_range: Optional[str],
    qpdf_path: str = "qpdf",
    run: Optional[Callable[..., subprocess.CompletedProcess]] = None,
) -> Path:
    """Return the PDF path to feed into ebook-convert considering page ranges.

    If ``page_range`` is falsy the original ``input_pdf`` path is returned. When
    a range is provided, qpdf is invoked to produce a temporary subset PDF
    inside ``workspace``. The created file path is returned so callers can pass
    it to downstream conversion steps.

    Args:
        input_pdf: Original PDF path selected by the user.
        workspace: Temporary directory where subset files are written.
        page_range: Raw page selection string (e.g. ``"1-5,8,10"``).
        qpdf_path: Executable name or path for qpdf.
        run: Optional callable compatible with ``subprocess.run`` used to invoke
            qpdf. Allows dependency injection for tests.

    Returns:
        Path to the PDF that should be converted (either original or subset).
    """
    source = _ensure_input_path(input_pdf)
    if not page_range or not page_range.strip():
        return source

    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    output = _build_output_path(workspace, source)
    command: Sequence[str] = [
        qpdf_path,
        str(source),
        str(output),
        "--pages",
        str(source),
        page_range.strip(),
        "--",
    ]

    runner = run or subprocess.run
    try:
        runner(command, check=True)
    except subprocess.CalledProcessError as exc:  # pragma: no cover - delegated
        raise PdfSubsetError(
            f"qpdf no pudo generar el subconjunto de páginas '{page_range}': {exc}"
        ) from exc
    except OSError as exc:
        raise PdfSubsetError(f"No se pudo ejecutar qpdf: {exc}") from exc

    return output

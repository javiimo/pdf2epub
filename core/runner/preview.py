"""Execution helpers to generate OEB previews via ebook-convert."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from core.configuration import TabConfiguration
from core.options.catalog import Catalog, get_catalog
from core.parser import OpfParserError, find_first_spine_html
from core.runner.cli_support import get_supported_flags
from core.runner.options_cli import build_convert_command
from core.runner.pdf_subset import PdfSubsetError, prepare_pdf_subset
from core.runner.temp_manager import TemporaryWorkspace

__all__ = ["PreviewError", "PreviewResult", "run_preview"]


def _ensure_catalog(catalog: Optional[Catalog]) -> Catalog:
    return catalog if catalog is not None else get_catalog()


@dataclass
class PreviewResult:
    """Outcome of a preview execution."""

    workspace: TemporaryWorkspace
    command: Sequence[str]
    oeb_output: Path
    subset_pdf: Path
    stdout: str
    stderr: str
    spine_first_html: Path
    skipped_options: Sequence[str] = ()


class PreviewError(RuntimeError):
    """Raised when ebook-convert preview fails."""

    def __init__(
        self,
        message: str,
        *,
        command: Sequence[str],
        stdout: str,
        stderr: str,
        returncode: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.command = list(command)
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
def run_preview(
    config: TabConfiguration,
    *,
    catalog: Optional[Catalog] = None,
    ebook_convert_path: str = "ebook-convert",
    qpdf_path: str = "qpdf",
    workspace_factory: Callable[[], TemporaryWorkspace] = TemporaryWorkspace,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> PreviewResult:
    """Execute ebook-convert to generate an OEB preview for the given config."""
    if not config.input_pdf:
        raise PreviewError(
            "Selecciona primero un PDF de entrada para previsualizar.",
            command=[],
            stdout="",
            stderr="",
            returncode=None,
        )

    active_catalog = _ensure_catalog(catalog)
    workspace = workspace_factory()
    try:
        subset_pdf = prepare_pdf_subset(
            config.input_pdf,
            workspace=workspace.path,
            page_range=config.page_range,
            qpdf_path=qpdf_path,
        )
    except PdfSubsetError as exc:
        workspace.cleanup()
        raise PreviewError(
            str(exc),
            command=[],
            stdout="",
            stderr="",
            returncode=None,
        ) from exc

    oeb_output = workspace.path / "preview-oeb"
    supported_flags = get_supported_flags(ebook_convert_path)
    skipped: List[str] = []
    command = build_convert_command(
        ebook_convert_path,
        subset_pdf,
        oeb_output,
        config,
        active_catalog,
        supported_flags=supported_flags,
        skipped=skipped,
    )

    try:
        completed = run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        workspace.cleanup()
        raise PreviewError(
            f"No se pudo ejecutar ebook-convert: {exc}",
            command=command,
            stdout="",
            stderr=str(exc),
            returncode=None,
        ) from exc

    stdout = completed.stdout or ""
    stderr = completed.stderr or ""

    if completed.returncode != 0:
        workspace.cleanup()
        raise PreviewError(
            f"ebook-convert finalizó con código {completed.returncode}.",
            command=command,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
        )

    if not oeb_output.exists():
        workspace.cleanup()
        raise PreviewError(
            "La previsualización no generó el directorio OEB esperado.",
            command=command,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
        )

    try:
        spine_first = find_first_spine_html(oeb_output)
    except OpfParserError as exc:
        workspace.cleanup()
        raise PreviewError(
            f"No se pudo interpretar content.opf: {exc}",
            command=command,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
        ) from exc

    return PreviewResult(
        workspace=workspace,
        command=command,
        oeb_output=oeb_output,
        subset_pdf=subset_pdf,
        stdout=stdout,
        stderr=stderr,
        spine_first_html=spine_first,
        skipped_options=tuple(skipped),
    )

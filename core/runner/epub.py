"""Helpers to generate final EPUB outputs via ebook-convert."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from core.configuration import TabConfiguration
from core.options.catalog import Catalog, get_catalog
from core.runner.cli_support import get_supported_flags
from core.runner.options_cli import build_convert_command
from core.runner.pdf_subset import PdfSubsetError, prepare_pdf_subset
from core.runner.temp_manager import TemporaryWorkspace

__all__ = ["ConversionError", "ConversionResult", "run_epub"]


@dataclass
class ConversionResult:
    command: Sequence[str]
    target: Path
    subset_pdf: Path
    stdout: str
    stderr: str
    skipped_options: Sequence[str] = ()


class ConversionError(RuntimeError):
    """Raised when ebook-convert fails to generate the EPUB."""

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


def _ensure_catalog(catalog: Optional[Catalog]) -> Catalog:
    return catalog if catalog is not None else get_catalog()


def run_epub(
    config: TabConfiguration,
    target: Path,
    *,
    catalog: Optional[Catalog] = None,
    ebook_convert_path: str = "ebook-convert",
    qpdf_path: str = "qpdf",
    workspace_factory: Callable[[], TemporaryWorkspace] = TemporaryWorkspace,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> ConversionResult:
    """Generate the EPUB file for the given configuration."""
    if not config.input_pdf:
        raise ConversionError(
            "Selecciona primero un PDF de entrada para generar el EPUB.",
            command=[],
            stdout="",
            stderr="",
            returncode=None,
        )

    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)

    active_catalog = _ensure_catalog(catalog)
    workspace = workspace_factory()
    try:
        subset_pdf = prepare_pdf_subset(
            config.input_pdf,
            workspace=workspace.path,
            page_range=config.page_range,
            qpdf_path=qpdf_path,
        )

        supported_flags = get_supported_flags(ebook_convert_path)
        skipped: List[str] = []
        command = build_convert_command(
            ebook_convert_path,
            subset_pdf,
            destination,
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
            raise ConversionError(
                f"No se pudo ejecutar ebook-convert: {exc}",
                command=command,
                stdout="",
                stderr=str(exc),
                returncode=None,
            ) from exc

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""

        if completed.returncode != 0:
            raise ConversionError(
                f"ebook-convert finalizó con código {completed.returncode}.",
                command=command,
                stdout=stdout,
                stderr=stderr,
                returncode=completed.returncode,
            )

        if not destination.exists():
            raise ConversionError(
                "La conversión no generó el archivo EPUB esperado.",
                command=command,
                stdout=stdout,
                stderr=stderr,
                returncode=completed.returncode,
            )

        return ConversionResult(
            command=command,
            target=destination,
            subset_pdf=subset_pdf,
            stdout=stdout,
            stderr=stderr,
            skipped_options=tuple(skipped),
        )
    except PdfSubsetError as exc:
        raise ConversionError(
            str(exc),
            command=[],
            stdout="",
            stderr="",
            returncode=None,
        ) from exc
    finally:
        workspace.cleanup()

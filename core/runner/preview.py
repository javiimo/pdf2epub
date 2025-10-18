"""Execution helpers to generate OEB previews via ebook-convert."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from core.configuration import TabConfiguration
from core.options.catalog import Catalog, get_catalog
from core.parser import OpfParserError, SpineItem, parse_spine
from core.runner.cli_support import get_supported_flags
from core.runner.options_cli import build_convert_command
from core.runner.pdf_subset import PdfSubsetError, prepare_pdf_subset
from core.runner.temp_manager import TemporaryWorkspace
from core.presets import apply_preset, get_presets

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
    spine_linear_items: Sequence[SpineItem] = ()
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
    # Ensure a sensible default device profile for OEB preview.
    # Apply Kobo base preset when no explicit output-profile is set.
    if "output-profile" not in (config.options or {}):
        presets = get_presets()
        kobo = next((p for p in presets if p.id == "base-kobo"), None)
        if kobo is not None:
            # Work on a shallow copy of the config so we don't mutate caller state
            cfg_options = dict(config.options)
            apply_preset(cfg_options, kobo)
            config = replace(config, options=cfg_options)
    workspace = workspace_factory()
    try:
        def subset_run(command: Sequence[str], *, check: bool = True) -> subprocess.CompletedProcess:
            return run(
                command,
                capture_output=True,
                text=True,
                check=check,
            )

        subset_pdf = prepare_pdf_subset(
            config.input_pdf,
            workspace=workspace.path,
            page_range=config.page_range,
            qpdf_path=qpdf_path,
            run=subset_run,
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
        spine_items = parse_spine(oeb_output)
    except OpfParserError as exc:
        workspace.cleanup()
        raise PreviewError(
            f"No se pudo interpretar content.opf: {exc}",
            command=command,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
        ) from exc

    linear_html: List[SpineItem] = []
    for entry in spine_items:
        if not entry.linear:
            continue
        if "html" not in entry.media_type.lower():
            continue
        if not entry.href.exists():
            workspace.cleanup()
            raise PreviewError(
                f"El archivo referenciado por '{entry.idref}' no existe: {entry.href}",
                command=command,
                stdout=stdout,
                stderr=stderr,
                returncode=completed.returncode,
            )
        linear_html.append(entry)

    if not linear_html:
        workspace.cleanup()
        raise PreviewError(
            "No se encontró ningún elemento HTML lineal en el spine.",
            command=command,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
        )

    spine_first = linear_html[0].href

    return PreviewResult(
        workspace=workspace,
        command=command,
        oeb_output=oeb_output,
        subset_pdf=subset_pdf,
        stdout=stdout,
        stderr=stderr,
        spine_first_html=spine_first,
        skipped_options=tuple(skipped),
        spine_linear_items=tuple(linear_html),
    )

"""Helpers to generate final EPUB outputs via ebook-convert.

This module provides utilities to generate an EPUB either directly from a
PDF (running the full conversion) or by packaging a previously generated
OEB directory (faster path after a preview).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, List, Optional, Sequence

from core.configuration import TabConfiguration
from core.options.catalog import Catalog, get_catalog
from core.runner.cli_support import get_supported_flags
from core.runner.options_cli import build_convert_command
from core.runner.pdf_preprocessor import (
    PreprocessError,
    PreprocessResult,
    options_from_extras,
    preprocess_pdf,
)
from core.runner.pdf_subset import PdfSubsetError, prepare_pdf_subset
from core.runner.temp_manager import TemporaryWorkspace

__all__ = [
    "ConversionError",
    "ConversionResult",
    "run_epub",
    "package_epub_from_oeb",
]


@dataclass
class ConversionResult:
    command: Sequence[str]
    target: Path
    subset_pdf: Path
    stdout: str
    stderr: str
    skipped_options: Sequence[str] = ()
    preprocess: Optional[PreprocessResult] = None


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


def _is_header_footer_detection_bug(stderr: str) -> bool:
    """Detect the calibre PDF reflow header/footer IndexError signature."""
    if not stderr:
        return False
    s = stderr.lower()
    return ("find_header_footer" in s) and ("indexerror" in s)


def _cleanup_pdftohtml_files(html_prefix: str, directory: Path) -> None:
    """Clean up files created by pdftohtml."""
    
    # pdftohtml creates multiple files with different patterns
    patterns = [
        f"{html_prefix}-html.html",
        f"{html_prefix}-001.png",
        f"{html_prefix}-002.png",
        f"{html_prefix}-1_1.png",
        f"{html_prefix}-2_2.png",
        f"{html_prefix}s.html",
        f"{html_prefix}ind.html",
        f"{html_prefix}.html",
        f"{html_prefix}.pdf",
    ]
    
    for pattern in patterns:
        file_path = directory / pattern
        if file_path.exists():
            try:
                file_path.unlink()
            except OSError:
                pass  # Ignore errors when cleaning up
    
    # Also clean up any files matching glob patterns
    for pattern in [f"{html_prefix}*.png", f"{html_prefix}*.html"]:
        for file_path in directory.glob(pattern):
            try:
                file_path.unlink()
            except OSError:
                pass  # Ignore errors when cleaning up


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
    preprocess_result: Optional[PreprocessResult] = None
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

        extras = getattr(config, "extras", {}) or {}
        options = options_from_extras(extras)
        if options.should_process():
            preprocess_result = preprocess_pdf(
                subset_pdf,
                workspace=workspace.path / "preprocess",
                options=options,
            )
            subset_pdf = preprocess_result.output_pdf

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

        if completed.returncode != 0 and _is_header_footer_detection_bug(stderr):
            # Fallback level 1: disable auto header/footer skip
            fb_options = dict(config.options)
            fb_options["pdf-header-skip"] = "0"
            fb_options["pdf-footer-skip"] = "0"
            fb_config = replace(config, options=fb_options)

            fb_skipped: List[str] = []
            fb_command = build_convert_command(
                ebook_convert_path,
                subset_pdf,
                destination,
                fb_config,
                active_catalog,
                supported_flags=supported_flags,
                skipped=fb_skipped,
            )

            try:
                fb_completed = run(
                    fb_command,
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except OSError:
                raise ConversionError(
                    "ebook-convert falló y el reintento con fallback también falló.",
                    command=fb_command,
                    stdout=stdout,
                    stderr=stderr,
                    returncode=completed.returncode,
                )

            if fb_completed.returncode == 0:
                command = fb_command
                stdout = fb_completed.stdout or ""
                stderr = fb_completed.stderr or ""
                skipped.extend(x for x in fb_skipped if x not in skipped)
            else:
                # Fallback level 2: force non-matching regexes
                if _is_header_footer_detection_bug(fb_completed.stderr or ""):
                    rx_options = dict(fb_config.options)
                    rx_options.setdefault("pdf-header-skip", "0")
                    rx_options.setdefault("pdf-footer-skip", "0")
                    rx_options["pdf-header-regex"] = "(?!)"
                    rx_options["pdf-footer-regex"] = "(?!)"
                    rx_config = replace(fb_config, options=rx_options)

                    rx_skipped: List[str] = []
                    rx_command = build_convert_command(
                        ebook_convert_path,
                        subset_pdf,
                        destination,
                        rx_config,
                        active_catalog,
                        supported_flags=supported_flags,
                        skipped=rx_skipped,
                    )

                    try:
                        rx_completed = run(
                            rx_command,
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                    except OSError:
                        raise ConversionError(
                            "ebook-convert falló y los reintentos con fallback también fallaron.",
                            command=rx_command,
                            stdout=stdout,
                            stderr=stderr,
                            returncode=completed.returncode,
                        )

                    if rx_completed.returncode == 0:
                        command = rx_command
                        stdout = rx_completed.stdout or ""
                        stderr = rx_completed.stderr or ""
                        for x in fb_skipped:
                            if x not in skipped:
                                skipped.append(x)
                        for x in rx_skipped:
                            if x not in skipped:
                                skipped.append(x)
                    else:
                        # Fallback level 3: disable all header/footer detection and use no-flow mode
                        if _is_header_footer_detection_bug(rx_completed.stderr or ""):
                            noflow_options = dict(rx_config.options)
                            noflow_options.setdefault("pdf-header-skip", "0")
                            noflow_options.setdefault("pdf-footer-skip", "0")
                            noflow_options.setdefault("pdf-header-regex", "(?!)")
                            noflow_options.setdefault("pdf-footer-regex", "(?!)")
                            # Try to disable the reflow engine completely
                            noflow_options["no-flow"] = True
                            noflow_config = replace(rx_config, options=noflow_options)

                            noflow_skipped: List[str] = []
                            noflow_command = build_convert_command(
                                ebook_convert_path,
                                subset_pdf,
                                destination,
                                noflow_config,
                                active_catalog,
                                supported_flags=supported_flags,
                                skipped=noflow_skipped,
                            )

                            try:
                                noflow_completed = run(
                                    noflow_command,
                                    capture_output=True,
                                    text=True,
                                    check=False,
                                )
                            except OSError:
                                raise ConversionError(
                                    "ebook-convert falló y todos los reintentos con fallback también fallaron.",
                                    command=noflow_command,
                                    stdout=stdout,
                                    stderr=stderr,
                                    returncode=completed.returncode,
                                )

                            if noflow_completed.returncode == 0:
                                command = noflow_command
                                stdout = noflow_completed.stdout or ""
                                stderr = noflow_completed.stderr or ""
                                for x in fb_skipped:
                                    if x not in skipped:
                                        skipped.append(x)
                                for x in rx_skipped:
                                    if x not in skipped:
                                        skipped.append(x)
                                for x in noflow_skipped:
                                    if x not in skipped:
                                        skipped.append(x)
                            else:
                                # Fallback level 4: use the pdftohtml engine instead of calibre
                                if _is_header_footer_detection_bug(noflow_completed.stderr or ""):
                                    pdftohtml_options = dict(noflow_options)
                                    # Use the pdftohtml engine which doesn't have the header/footer detection bug
                                    pdftohtml_options["pdf-engine"] = "pdftohtml"
                                    pdftohtml_config = replace(noflow_config, options=pdftohtml_options)

                                    pdftohtml_skipped: List[str] = []
                                    pdftohtml_command = build_convert_command(
                                        ebook_convert_path,
                                        subset_pdf,
                                        destination,
                                        pdftohtml_config,
                                        active_catalog,
                                        supported_flags=supported_flags,
                                        skipped=pdftohtml_skipped,
                                    )

                                    try:
                                        pdftohtml_completed = run(
                                            pdftohtml_command,
                                            capture_output=True,
                                            text=True,
                                            check=False,
                                        )
                                    except OSError:
                                        raise ConversionError(
                                            "ebook-convert falló y todos los reintentos con fallback también fallaron.",
                                            command=pdftohtml_command,
                                            stdout=stdout,
                                            stderr=stderr,
                                            returncode=completed.returncode,
                                        )

                                    if pdftohtml_completed.returncode == 0:
                                        command = pdftohtml_command
                                        stdout = pdftohtml_completed.stdout or ""
                                        stderr = pdftohtml_completed.stderr or ""
                                        for x in fb_skipped:
                                            if x not in skipped:
                                                skipped.append(x)
                                        for x in rx_skipped:
                                            if x not in skipped:
                                                skipped.append(x)
                                        for x in noflow_skipped:
                                            if x not in skipped:
                                                skipped.append(x)
                                        for x in pdftohtml_skipped:
                                            if x not in skipped:
                                                skipped.append(x)
                                    else:
                                        # Fallback level 5: use pdftohtml engine with no-chapters
                                        if _is_header_footer_detection_bug(pdftohtml_completed.stderr or ""):
                                            nochapters_options = dict(pdftohtml_options)
                                            nochapters_options["pdf-engine"] = "pdftohtml"
                                            nochapters_options["no-chapters-in-toc"] = True
                                            nochapters_options["chapter"] = "/"  # Disable chapter detection completely
                                            nochapters_config = replace(pdftohtml_config, options=nochapters_options)

                                            nochapters_skipped: List[str] = []
                                            nochapters_command = build_convert_command(
                                                ebook_convert_path,
                                                subset_pdf,
                                                destination,
                                                nochapters_config,
                                                active_catalog,
                                                supported_flags=supported_flags,
                                                skipped=nochapters_skipped,
                                            )

                                            try:
                                                nochapters_completed = run(
                                                    nochapters_command,
                                                    capture_output=True,
                                                    text=True,
                                                    check=False,
                                                )
                                            except OSError:
                                                raise ConversionError(
                                                    "ebook-convert falló y todos los reintentos con fallback también fallaron.",
                                                    command=nochapters_command,
                                                    stdout=stdout,
                                                    stderr=stderr,
                                                    returncode=completed.returncode,
                                                )

                                            if nochapters_completed.returncode == 0:
                                                command = nochapters_command
                                                stdout = nochapters_completed.stdout or ""
                                                stderr = nochapters_completed.stderr or ""
                                                for x in fb_skipped:
                                                    if x not in skipped:
                                                        skipped.append(x)
                                                for x in rx_skipped:
                                                    if x not in skipped:
                                                        skipped.append(x)
                                                for x in noflow_skipped:
                                                    if x not in skipped:
                                                        skipped.append(x)
                                                for x in pdftohtml_skipped:
                                                    if x not in skipped:
                                                        skipped.append(x)
                                                for x in nochapters_skipped:
                                                    if x not in skipped:
                                                        skipped.append(x)
                                            else:
                                                # Fallback level 6: try with minimal options and no structure detection
                                                if _is_header_footer_detection_bug(nochapters_completed.stderr or ""):
                                                    minimal_options = {}
                                                    # Use only the most basic options that are absolutely necessary
                                                    minimal_options["chapter"] = "/"  # Disable chapter detection
                                                    minimal_options["no-chapters-in-toc"] = True
                                                    minimal_options["pdf-engine"] = "pdftohtml"
                                                    minimal_options["disable-heuristics"] = True  # Disable all heuristic processing
                                                    minimal_options["disable-all-heuristics"] = True  # Extra disable
                                                    
                                                    # Copy only essential formatting options from original config
                                                    essential_opts = ["base_font_size", "font_size_mapping", "change_justification",
                                                                     "output_profile", "minimum_line_height"]
                                                    for opt in essential_opts:
                                                        if opt in config.options:
                                                            minimal_options[opt] = config.options[opt]
                                                    
                                                    minimal_config = replace(config, options=minimal_options)

                                                    minimal_skipped: List[str] = []
                                                    minimal_command = build_convert_command(
                                                        ebook_convert_path,
                                                        subset_pdf,
                                                        destination,
                                                        minimal_config,
                                                        active_catalog,
                                                        supported_flags=supported_flags,
                                                        skipped=minimal_skipped,
                                                    )

                                                    try:
                                                        minimal_completed = run(
                                                            minimal_command,
                                                            capture_output=True,
                                                            text=True,
                                                            check=False,
                                                        )
                                                    except OSError:
                                                        raise ConversionError(
                                                            "ebook-convert falló y todos los reintentos con fallback también fallaron.",
                                                            command=minimal_command,
                                                            stdout=stdout,
                                                            stderr=stderr,
                                                            returncode=completed.returncode,
                                                        )

                                                    if minimal_completed.returncode == 0:
                                                        command = minimal_command
                                                        stdout = minimal_completed.stdout or ""
                                                        stderr = minimal_completed.stderr or ""
                                                        for x in fb_skipped:
                                                            if x not in skipped:
                                                                skipped.append(x)
                                                        for x in rx_skipped:
                                                            if x not in skipped:
                                                                skipped.append(x)
                                                        for x in noflow_skipped:
                                                            if x not in skipped:
                                                                skipped.append(x)
                                                        for x in pdftohtml_skipped:
                                                            if x not in skipped:
                                                                skipped.append(x)
                                                        for x in nochapters_skipped:
                                                            if x not in skipped:
                                                                skipped.append(x)
                                                        for x in minimal_skipped:
                                                            if x not in skipped:
                                                                skipped.append(x)
                                                    else:
                                                        # Fallback level 7: try two-step conversion via pdftohtml
                                                        if _is_header_footer_detection_bug(minimal_completed.stderr or ""):
                                                            # Use pdftohtml to convert PDF to HTML first
                                                            html_prefix = destination.with_suffix('').name + "-pdftohtml"
                                                            html_output = destination.parent / f"{html_prefix}-html.html"
                                                            
                                                            try:
                                                                # Use pdftohtml to convert PDF to HTML
                                                                pdftohtml_cmd = ["pdftohtml", "-s", str(subset_pdf), str(html_prefix)]
                                                                pdftohtml_completed = run(
                                                                    pdftohtml_cmd,
                                                                    capture_output=True,
                                                                    text=True,
                                                                    check=False,
                                                                )
                                                                
                                                                # pdftohtml creates multiple files, we need the main HTML file
                                                                if html_output.exists():
                                                                    # Now convert HTML to EPUB
                                                                    epub_from_html_options = {}
                                                                    # Copy only essential EPUB options
                                                                    for opt in ["base_font_size", "font_size_mapping", "change_justification",
                                                                             "output_profile", "minimum_line_height"]:
                                                                        if opt in config.options:
                                                                            epub_from_html_options[opt] = config.options[opt]
                                                                            
                                                                    epub_from_html_config = replace(config, options=epub_from_html_options)

                                                                    epub_from_html_skipped: List[str] = []
                                                                    epub_from_html_command = build_convert_command(
                                                                        ebook_convert_path,
                                                                        html_output,
                                                                        destination,
                                                                        epub_from_html_config,
                                                                        active_catalog,
                                                                        supported_flags=supported_flags,
                                                                        skipped=epub_from_html_skipped,
                                                                    )

                                                                    try:
                                                                        epub_from_html_completed = run(
                                                                            epub_from_html_command,
                                                                            capture_output=True,
                                                                            text=True,
                                                                            check=False,
                                                                        )
                                                                    except OSError:
                                                                        # Clean up HTML files and raise error
                                                                        _cleanup_pdftohtml_files(html_prefix, destination.parent)
                                                                        raise ConversionError(
                                                                            "ebook-convert falló y todos los reintentos con fallback también fallaron.",
                                                                            command=epub_from_html_command,
                                                                            stdout=stdout,
                                                                            stderr=stderr,
                                                                            returncode=completed.returncode,
                                                                        )

                                                                    # Clean up HTML files
                                                                    _cleanup_pdftohtml_files(html_prefix, destination.parent)

                                                                    # Check if conversion succeeded even with warnings (non-zero exit code)
                                                                    if epub_from_html_completed.returncode == 0 or destination.exists():
                                                                        command = epub_from_html_command
                                                                        stdout = epub_from_html_completed.stdout or ""
                                                                        stderr = epub_from_html_completed.stderr or ""
                                                                        for x in fb_skipped:
                                                                            if x not in skipped:
                                                                                skipped.append(x)
                                                                        for x in rx_skipped:
                                                                            if x not in skipped:
                                                                                skipped.append(x)
                                                                        for x in noflow_skipped:
                                                                            if x not in skipped:
                                                                                skipped.append(x)
                                                                        for x in pdftohtml_skipped:
                                                                            if x not in skipped:
                                                                                skipped.append(x)
                                                                        for x in nochapters_skipped:
                                                                            if x not in skipped:
                                                                                skipped.append(x)
                                                                        for x in minimal_skipped:
                                                                            if x not in skipped:
                                                                                skipped.append(x)
                                                                        for x in epub_from_html_skipped:
                                                                            if x not in skipped:
                                                                                skipped.append(x)
                                                                    else:
                                                                        combined_stderr = (
                                                                            (stderr or "").rstrip()
                                                                            + "\n\n[Fallback intentado: pdf-header-skip=0, pdf-footer-skip=0]\n"
                                                                            + (fb_completed.stderr or "")
                                                                            + "\n\n[Fallback 2 intentado: regex de cabecera/pie vacíos]\n"
                                                                            + (rx_completed.stderr or "")
                                                                            + "\n\n[Fallback 3 intentado: no-flow=True]\n"
                                                                            + (noflow_completed.stderr or "")
                                                                            + "\n\n[Fallback 4 intentado: pdf-engine=pdftohtml]\n"
                                                                            + (pdftohtml_completed.stderr or "")
                                                                            + "\n\n[Fallback 5 intentado: pdf-engine=pdftohtml + no-chapters]\n"
                                                                            + (nochapters_completed.stderr or "")
                                                                            + "\n\n[Fallback 6 intentado: opciones mínimas + heurísticas desactivadas]\n"
                                                                            + (minimal_completed.stderr or "")
                                                                            + "\n\n[Fallback 7a intentado: conversión PDF->HTML con pdftohtml]\n"
                                                                            + (pdftohtml_completed.stderr or "")
                                                                            + "\n\n[Fallback 7b intentado: conversión HTML->EPUB]\n"
                                                                            + (epub_from_html_completed.stderr or "")
                                                                        )
                                                                        combined_stdout = (
                                                                            (stdout or "").rstrip()
                                                                            + "\n\n[Fallback intentado]\n"
                                                                            + (fb_completed.stdout or "")
                                                                            + "\n\n[Fallback 2 intentado]\n"
                                                                            + (rx_completed.stdout or "")
                                                                            + "\n\n[Fallback 3 intentado]\n"
                                                                            + (noflow_completed.stdout or "")
                                                                            + "\n\n[Fallback 4 intentado]\n"
                                                                            + (pdftohtml_completed.stdout or "")
                                                                            + "\n\n[Fallback 5 intentado]\n"
                                                                            + (nochapters_completed.stdout or "")
                                                                            + "\n\n[Fallback 6 intentado]\n"
                                                                            + (minimal_completed.stdout or "")
                                                                            + "\n\n[Fallback 7a intentado]\n"
                                                                            + (pdftohtml_completed.stdout or "")
                                                                            + "\n\n[Fallback 7b intentado]\n"
                                                                            + (epub_from_html_completed.stdout or "")
                                                                        )
                                                                        raise ConversionError(
                                                                            f"ebook-convert falló (y todos los fallbacks también) con código {epub_from_html_completed.returncode}.",
                                                                            command=epub_from_html_command,
                                                                            stdout=combined_stderr,
                                                                            stderr=combined_stdout,
                                                                            returncode=epub_from_html_completed.returncode,
                                                                        )
                                                                else:
                                                                    combined_stderr = (
                                                                        (stderr or "").rstrip()
                                                                        + "\n\n[Fallback intentado: pdf-header-skip=0, pdf-footer-skip=0]\n"
                                                                        + (fb_completed.stderr or "")
                                                                        + "\n\n[Fallback 2 intentado: regex de cabecera/pie vacíos]\n"
                                                                        + (rx_completed.stderr or "")
                                                                        + "\n\n[Fallback 3 intentado: no-flow=True]\n"
                                                                        + (noflow_completed.stderr or "")
                                                                        + "\n\n[Fallback 4 intentado: pdf-engine=pdftohtml]\n"
                                                                        + (pdftohtml_completed.stderr or "")
                                                                        + "\n\n[Fallback 5 intentado: pdf-engine=pdftohtml + no-chapters]\n"
                                                                        + (nochapters_completed.stderr or "")
                                                                        + "\n\n[Fallback 6 intentado: opciones mínimas + heurísticas desactivadas]\n"
                                                                        + (minimal_completed.stderr or "")
                                                                        + "\n\n[Fallback 7a intentado: conversión PDF->HTML con pdftohtml]\n"
                                                                        + (pdftohtml_completed.stderr or "")
                                                                    )
                                                                    combined_stdout = (
                                                                        (stdout or "").rstrip()
                                                                        + "\n\n[Fallback intentado]\n"
                                                                        + (fb_completed.stdout or "")
                                                                        + "\n\n[Fallback 2 intentado]\n"
                                                                        + (rx_completed.stdout or "")
                                                                        + "\n\n[Fallback 3 intentado]\n"
                                                                        + (noflow_completed.stdout or "")
                                                                        + "\n\n[Fallback 4 intentado]\n"
                                                                        + (pdftohtml_completed.stdout or "")
                                                                        + "\n\n[Fallback 5 intentado]\n"
                                                                        + (nochapters_completed.stdout or "")
                                                                        + "\n\n[Fallback 6 intentado]\n"
                                                                        + (minimal_completed.stdout or "")
                                                                        + "\n\n[Fallback 7a intentado]\n"
                                                                        + (pdftohtml_completed.stdout or "")
                                                                    )
                                                                    raise ConversionError(
                                                                        f"ebook-convert falló (y todos los fallbacks también) con código {pdftohtml_completed.returncode}.",
                                                                        command=pdftohtml_cmd,
                                                                        stdout=combined_stderr,
                                                                        stderr=combined_stdout,
                                                                        returncode=pdftohtml_completed.returncode,
                                                                    )
                                                            except OSError:
                                                                raise ConversionError(
                                                                    "ebook-convert falló y todos los reintentos con fallback también fallaron.",
                                                                    command=["pdftohtml"],
                                                                    stdout=stdout,
                                                                    stderr=stderr,
                                                                    returncode=completed.returncode,
                                                                )
                                                        else:
                                                            combined_stderr = (
                                                                (stderr or "").rstrip()
                                                                + "\n\n[Fallback intentado: pdf-header-skip=0, pdf-footer-skip=0]\n"
                                                                + (fb_completed.stderr or "")
                                                                + "\n\n[Fallback 2 intentado: regex de cabecera/pie vacíos]\n"
                                                                + (rx_completed.stderr or "")
                                                                + "\n\n[Fallback 3 intentado: no-flow=True]\n"
                                                                + (noflow_completed.stderr or "")
                                                                + "\n\n[Fallback 4 intentado: pdf-engine=pdftohtml]\n"
                                                                + (pdftohtml_completed.stderr or "")
                                                                + "\n\n[Fallback 5 intentado: pdf-engine=pdftohtml + no-chapters]\n"
                                                                + (nochapters_completed.stderr or "")
                                                                + "\n\n[Fallback 6 intentado: opciones mínimas + heurísticas desactivadas]\n"
                                                                + (minimal_completed.stderr or "")
                                                            )
                                                            combined_stdout = (
                                                                (stdout or "").rstrip()
                                                                + "\n\n[Fallback intentado]\n"
                                                                + (fb_completed.stdout or "")
                                                                + "\n\n[Fallback 2 intentado]\n"
                                                                + (rx_completed.stdout or "")
                                                                + "\n\n[Fallback 3 intentado]\n"
                                                                + (noflow_completed.stdout or "")
                                                                + "\n\n[Fallback 4 intentado]\n"
                                                                + (pdftohtml_completed.stdout or "")
                                                                + "\n\n[Fallback 5 intentado]\n"
                                                                + (nochapters_completed.stdout or "")
                                                                + "\n\n[Fallback 6 intentado]\n"
                                                                + (minimal_completed.stdout or "")
                                                            )
                                                            raise ConversionError(
                                                                f"ebook-convert falló (y todos los fallbacks también) con código {minimal_completed.returncode}.",
                                                                command=minimal_command,
                                                                stdout=combined_stderr,
                                                                stderr=combined_stdout,
                                                                returncode=minimal_completed.returncode,
                                                            )
                                                else:
                                                    combined_stderr = (
                                                        (stderr or "").rstrip()
                                                        + "\n\n[Fallback intentado: pdf-header-skip=0, pdf-footer-skip=0]\n"
                                                        + (fb_completed.stderr or "")
                                                        + "\n\n[Fallback 2 intentado: regex de cabecera/pie vacíos]\n"
                                                        + (rx_completed.stderr or "")
                                                        + "\n\n[Fallback 3 intentado: no-flow=True]\n"
                                                        + (noflow_completed.stderr or "")
                                                        + "\n\n[Fallback 4 intentado: pdf-engine=pdftohtml]\n"
                                                        + (pdftohtml_completed.stderr or "")
                                                        + "\n\n[Fallback 5 intentado: pdf-engine=pdftohtml + no-chapters]\n"
                                                        + (nochapters_completed.stderr or "")
                                                    )
                                                    combined_stdout = (
                                                        (stdout or "").rstrip()
                                                        + "\n\n[Fallback intentado]\n"
                                                        + (fb_completed.stdout or "")
                                                        + "\n\n[Fallback 2 intentado]\n"
                                                        + (rx_completed.stdout or "")
                                                        + "\n\n[Fallback 3 intentado]\n"
                                                        + (noflow_completed.stdout or "")
                                                        + "\n\n[Fallback 4 intentado]\n"
                                                        + (pdftohtml_completed.stdout or "")
                                                        + "\n\n[Fallback 5 intentado]\n"
                                                        + (nochapters_completed.stdout or "")
                                                    )
                                                    raise ConversionError(
                                                        f"ebook-convert falló (y los fallbacks 1-4 también) con código {pdftohtml_completed.returncode}.",
                                                        command=pdftohtml_command,
                                                        stdout=combined_stdout,
                                                        stderr=combined_stderr,
                                                        returncode=pdftohtml_completed.returncode,
                                                    )
                                        else:
                                            combined_stderr = (
                                                (stderr or "").rstrip()
                                                + "\n\n[Fallback intentado: pdf-header-skip=0, pdf-footer-skip=0]\n"
                                                + (fb_completed.stderr or "")
                                                + "\n\n[Fallback 2 intentado: regex de cabecera/pie vacíos]\n"
                                                + (rx_completed.stderr or "")
                                                + "\n\n[Fallback 3 intentado: no-flow=True]\n"
                                                + (noflow_completed.stderr or "")
                                            )
                                            combined_stdout = (
                                                (stdout or "").rstrip()
                                                + "\n\n[Fallback intentado]\n"
                                                + (fb_completed.stdout or "")
                                                + "\n\n[Fallback 2 intentado]\n"
                                                + (rx_completed.stdout or "")
                                                + "\n\n[Fallback 3 intentado]\n"
                                                + (noflow_completed.stdout or "")
                                            )
                                            raise ConversionError(
                                                f"ebook-convert falló (y los fallbacks 1, 2 y 3 también) con código {noflow_completed.returncode}.",
                                                command=noflow_command,
                                                stdout=combined_stdout,
                                                stderr=combined_stderr,
                                                returncode=noflow_completed.returncode,
                                            )
                                else:
                                    combined_stderr = (
                                        (stderr or "").rstrip()
                                        + "\n\n[Fallback intentado: pdf-header-skip=0, pdf-footer-skip=0]\n"
                                        + (fb_completed.stderr or "")
                                        + "\n\n[Fallback 2 intentado: regex de cabecera/pie vacíos]\n"
                                        + (rx_completed.stderr or "")
                                    )
                                    combined_stdout = (
                                        (stdout or "").rstrip()
                                        + "\n\n[Fallback intentado]\n"
                                        + (fb_completed.stdout or "")
                                        + "\n\n[Fallback 2 intentado]\n"
                                        + (rx_completed.stdout or "")
                                    )
                                    raise ConversionError(
                                        f"ebook-convert falló (y los fallbacks 1 y 2 también) con código {rx_completed.returncode}.",
                                        command=rx_command,
                                        stdout=combined_stdout,
                                        stderr=combined_stderr,
                                        returncode=rx_completed.returncode,
                                    )
                        else:
                            combined_stderr = (
                                (stderr or "").rstrip()
                                + "\n\n[Fallback intentado: pdf-header-skip=0, pdf-footer-skip=0]\n"
                                + (fb_completed.stderr or "")
                            )
                            combined_stdout = (
                                (stdout or "").rstrip()
                                + "\n\n[Fallback intentado]\n"
                                + (fb_completed.stdout or "")
                            )
                            raise ConversionError(
                                f"ebook-convert falló (y el fallback también) con código {fb_completed.returncode}.",
                                command=fb_command,
                                stdout=combined_stdout,
                                stderr=combined_stderr,
                                returncode=fb_completed.returncode,
                            )

        # Check if conversion succeeded even with warnings (non-zero exit code)
        if completed.returncode != 0 and not destination.exists():
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
            preprocess=preprocess_result,
        )
    except PdfSubsetError as exc:
        raise ConversionError(
            str(exc),
            command=[],
            stdout="",
            stderr="",
            returncode=None,
        ) from exc
    except PreprocessError as exc:
        raise ConversionError(
            str(exc),
            command=[],
            stdout="",
            stderr="",
            returncode=None,
        ) from exc
    finally:
        workspace.cleanup()


def package_epub_from_oeb(
    config: TabConfiguration,
    oeb_dir: Path,
    target: Path,
    *,
    catalog: Optional[Catalog] = None,
    ebook_convert_path: str = "ebook-convert",
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> ConversionResult:
    """Package an EPUB from an existing OEB directory via ebook-convert.

    This is the fast path used after a preview step. The input directory must
    contain a valid ``content.opf``.
    """
    destination = Path(target)
    destination.parent.mkdir(parents=True, exist_ok=True)

    oeb_path = Path(oeb_dir)
    if not oeb_path.exists() or not oeb_path.is_dir():
        raise ConversionError(
            f"El directorio OEB no existe: {oeb_path}",
            command=[],
            stdout="",
            stderr="",
            returncode=None,
        )

    # Basic sanity check: make sure content.opf exists
    if not (oeb_path / "content.opf").exists():
        raise ConversionError(
            f"No se encontró 'content.opf' en: {oeb_path}",
            command=[],
            stdout="",
            stderr="",
            returncode=None,
        )

    active_catalog = _ensure_catalog(catalog)
    supported_flags = get_supported_flags(ebook_convert_path)
    skipped: List[str] = []
    command = build_convert_command(
        ebook_convert_path,
        oeb_path / "content.opf",
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

    # Apply the same fallback mechanisms as run_epub for header/footer detection bug
    if completed.returncode != 0 and _is_header_footer_detection_bug(stderr):
        # Fallback level 1: disable auto header/footer skip
        fb_options = dict(config.options)
        fb_options["pdf-header-skip"] = "0"
        fb_options["pdf-footer-skip"] = "0"
        fb_config = replace(config, options=fb_options)

        fb_skipped: List[str] = []
        fb_command = build_convert_command(
            ebook_convert_path,
            oeb_path / "content.opf",
            destination,
            fb_config,
            active_catalog,
            supported_flags=supported_flags,
            skipped=fb_skipped,
        )

        try:
            fb_completed = run(
                fb_command,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            raise ConversionError(
                "ebook-convert falló y el reintento con fallback también falló.",
                command=fb_command,
                stdout=stdout,
                stderr=stderr,
                returncode=completed.returncode,
            )

        if fb_completed.returncode == 0:
            command = fb_command
            stdout = fb_completed.stdout or ""
            stderr = fb_completed.stderr or ""
            skipped.extend(x for x in fb_skipped if x not in skipped)
        else:
            # Fallback level 2: force non-matching regexes
            if _is_header_footer_detection_bug(fb_completed.stderr or ""):
                rx_options = dict(fb_config.options)
                rx_options.setdefault("pdf-header-skip", "0")
                rx_options.setdefault("pdf-footer-skip", "0")
                rx_options["pdf-header-regex"] = "(?!)"
                rx_options["pdf-footer-regex"] = "(?!)"
                rx_config = replace(fb_config, options=rx_options)

                rx_skipped: List[str] = []
                rx_command = build_convert_command(
                    ebook_convert_path,
                    oeb_path / "content.opf",
                    destination,
                    rx_config,
                    active_catalog,
                    supported_flags=supported_flags,
                    skipped=rx_skipped,
                )

                try:
                    rx_completed = run(
                        rx_command,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                except OSError:
                    raise ConversionError(
                        "ebook-convert falló y los reintentos con fallback también fallaron.",
                        command=rx_command,
                        stdout=stdout,
                        stderr=stderr,
                        returncode=completed.returncode,
                    )

                if rx_completed.returncode == 0:
                    command = rx_command
                    stdout = rx_completed.stdout or ""
                    stderr = rx_completed.stderr or ""
                    for x in fb_skipped:
                        if x not in skipped:
                            skipped.append(x)
                    for x in rx_skipped:
                        if x not in skipped:
                            skipped.append(x)
                else:
                    # Fallback level 3: disable all header/footer detection and use no-flow mode
                    if _is_header_footer_detection_bug(rx_completed.stderr or ""):
                        noflow_options = dict(rx_config.options)
                        noflow_options.setdefault("pdf-header-skip", "0")
                        noflow_options.setdefault("pdf-footer-skip", "0")
                        noflow_options.setdefault("pdf-header-regex", "(?!)")
                        noflow_options.setdefault("pdf-footer-regex", "(?!)")
                        # Try to disable the reflow engine completely
                        noflow_options["no-flow"] = True
                        noflow_config = replace(rx_config, options=noflow_options)

                        noflow_skipped: List[str] = []
                        noflow_command = build_convert_command(
                            ebook_convert_path,
                            oeb_path / "content.opf",
                            destination,
                            noflow_config,
                            active_catalog,
                            supported_flags=supported_flags,
                            skipped=noflow_skipped,
                        )

                        try:
                            noflow_completed = run(
                                noflow_command,
                                capture_output=True,
                                text=True,
                                check=False,
                            )
                        except OSError:
                            raise ConversionError(
                                "ebook-convert falló y todos los reintentos con fallback también fallaron.",
                                command=noflow_command,
                                stdout=stdout,
                                stderr=stderr,
                                returncode=completed.returncode,
                            )

                        if noflow_completed.returncode == 0:
                            command = noflow_command
                            stdout = noflow_completed.stdout or ""
                            stderr = noflow_completed.stderr or ""
                            for x in fb_skipped:
                                if x not in skipped:
                                    skipped.append(x)
                            for x in rx_skipped:
                                if x not in skipped:
                                    skipped.append(x)
                            for x in noflow_skipped:
                                if x not in skipped:
                                    skipped.append(x)
                        else:
                            # Fallback level 4: use the pdftohtml engine instead of calibre
                            if _is_header_footer_detection_bug(noflow_completed.stderr or ""):
                                pdftohtml_options = dict(noflow_options)
                                # Use the pdftohtml engine which doesn't have the header/footer detection bug
                                pdftohtml_options["pdf-engine"] = "pdftohtml"
                                pdftohtml_config = replace(noflow_config, options=pdftohtml_options)

                                pdftohtml_skipped: List[str] = []
                                pdftohtml_command = build_convert_command(
                                    ebook_convert_path,
                                    oeb_path / "content.opf",
                                    destination,
                                    pdftohtml_config,
                                    active_catalog,
                                    supported_flags=supported_flags,
                                    skipped=pdftohtml_skipped,
                                )

                                try:
                                    pdftohtml_completed = run(
                                        pdftohtml_command,
                                        capture_output=True,
                                        text=True,
                                        check=False,
                                    )
                                except OSError:
                                    raise ConversionError(
                                        "ebook-convert falló y todos los reintentos con fallback también fallaron.",
                                        command=pdftohtml_command,
                                        stdout=stdout,
                                        stderr=stderr,
                                        returncode=completed.returncode,
                                    )

                                if pdftohtml_completed.returncode == 0:
                                    command = pdftohtml_command
                                    stdout = pdftohtml_completed.stdout or ""
                                    stderr = pdftohtml_completed.stderr or ""
                                    for x in fb_skipped:
                                        if x not in skipped:
                                            skipped.append(x)
                                    for x in rx_skipped:
                                        if x not in skipped:
                                            skipped.append(x)
                                    for x in noflow_skipped:
                                        if x not in skipped:
                                            skipped.append(x)
                                    for x in pdftohtml_skipped:
                                        if x not in skipped:
                                            skipped.append(x)
                                else:
                                    # Fallback level 5: use pdftohtml engine with no-chapters
                                    if _is_header_footer_detection_bug(pdftohtml_completed.stderr or ""):
                                        nochapters_options = dict(pdftohtml_options)
                                        nochapters_options["pdf-engine"] = "pdftohtml"
                                        nochapters_options["no-chapters-in-toc"] = True
                                        nochapters_options["chapter"] = "/"  # Disable chapter detection completely
                                        nochapters_config = replace(pdftohtml_config, options=nochapters_options)

                                        nochapters_skipped: List[str] = []
                                        nochapters_command = build_convert_command(
                                            ebook_convert_path,
                                            oeb_path / "content.opf",
                                            destination,
                                            nochapters_config,
                                            active_catalog,
                                            supported_flags=supported_flags,
                                            skipped=nochapters_skipped,
                                        )

                                        try:
                                            nochapters_completed = run(
                                                nochapters_command,
                                                capture_output=True,
                                                text=True,
                                                check=False,
                                            )
                                        except OSError:
                                            raise ConversionError(
                                                "ebook-convert falló y todos los reintentos con fallback también fallaron.",
                                                command=nochapters_command,
                                                stdout=stdout,
                                                stderr=stderr,
                                                returncode=completed.returncode,
                                            )

                                        if nochapters_completed.returncode == 0:
                                            command = nochapters_command
                                            stdout = nochapters_completed.stdout or ""
                                            stderr = nochapters_completed.stderr or ""
                                            for x in fb_skipped:
                                                if x not in skipped:
                                                    skipped.append(x)
                                            for x in rx_skipped:
                                                if x not in skipped:
                                                    skipped.append(x)
                                            for x in noflow_skipped:
                                                if x not in skipped:
                                                    skipped.append(x)
                                            for x in pdftohtml_skipped:
                                                if x not in skipped:
                                                    skipped.append(x)
                                            for x in nochapters_skipped:
                                                if x not in skipped:
                                                    skipped.append(x)
                                        else:
                                            # Fallback level 6: try with minimal options and no structure detection
                                            if _is_header_footer_detection_bug(nochapters_completed.stderr or ""):
                                                minimal_options = {}
                                                # Use only the most basic options that are absolutely necessary
                                                minimal_options["chapter"] = "/"  # Disable chapter detection
                                                minimal_options["no-chapters-in-toc"] = True
                                                minimal_options["pdf-engine"] = "pdftohtml"
                                                minimal_options["disable-heuristics"] = True  # Disable all heuristic processing
                                                minimal_options["disable-all-heuristics"] = True  # Extra disable
                                                
                                                # Copy only essential formatting options from original config
                                                essential_opts = ["base_font_size", "font_size_mapping", "change_justification",
                                                                "output_profile", "minimum_line_height"]
                                                for opt in essential_opts:
                                                    if opt in config.options:
                                                        minimal_options[opt] = config.options[opt]
                                                
                                                minimal_config = replace(config, options=minimal_options)

                                                minimal_skipped: List[str] = []
                                                minimal_command = build_convert_command(
                                                    ebook_convert_path,
                                                    oeb_path / "content.opf",
                                                    destination,
                                                    minimal_config,
                                                    active_catalog,
                                                    supported_flags=supported_flags,
                                                    skipped=minimal_skipped,
                                                )

                                                try:
                                                    minimal_completed = run(
                                                        minimal_command,
                                                        capture_output=True,
                                                        text=True,
                                                        check=False,
                                                    )
                                                except OSError:
                                                    raise ConversionError(
                                                        "ebook-convert falló y todos los reintentos con fallback también fallaron.",
                                                        command=minimal_command,
                                                        stdout=stdout,
                                                        stderr=stderr,
                                                        returncode=completed.returncode,
                                                    )

                                                if minimal_completed.returncode == 0:
                                                    command = minimal_command
                                                    stdout = minimal_completed.stdout or ""
                                                    stderr = minimal_completed.stderr or ""
                                                    for x in fb_skipped:
                                                        if x not in skipped:
                                                            skipped.append(x)
                                                    for x in rx_skipped:
                                                        if x not in skipped:
                                                            skipped.append(x)
                                                    for x in noflow_skipped:
                                                        if x not in skipped:
                                                            skipped.append(x)
                                                    for x in pdftohtml_skipped:
                                                        if x not in skipped:
                                                            skipped.append(x)
                                                    for x in nochapters_skipped:
                                                        if x not in skipped:
                                                            skipped.append(x)
                                                    for x in minimal_skipped:
                                                        if x not in skipped:
                                                            skipped.append(x)
                                                else:
                                                    # Fallback level 7: try two-step conversion via pdftohtml
                                                    if _is_header_footer_detection_bug(minimal_completed.stderr or ""):
                                                        # Use pdftohtml to convert PDF to HTML first
                                                        html_prefix = destination.with_suffix('').name + "-pdftohtml"
                                                        html_output = destination.parent / f"{html_prefix}-html.html"
                                                        
                                                        try:
                                                            # Use pdftohtml to convert PDF to HTML
                                                            pdftohtml_cmd = ["pdftohtml", "-s", str(oeb_path / "content.opf"), str(html_prefix)]
                                                            pdftohtml_completed = run(
                                                                pdftohtml_cmd,
                                                                capture_output=True,
                                                                text=True,
                                                                check=False,
                                                            )
                                                            
                                                            # pdftohtml creates multiple files, we need the main HTML file
                                                            if html_output.exists():
                                                                # Now convert HTML to EPUB
                                                                epub_from_html_options = {}
                                                                # Copy only essential EPUB options
                                                                for opt in ["base_font_size", "font_size_mapping", "change_justification",
                                                                         "output_profile", "minimum_line_height"]:
                                                                    if opt in config.options:
                                                                        epub_from_html_options[opt] = config.options[opt]
                                                                        
                                                                epub_from_html_config = replace(config, options=epub_from_html_options)

                                                                epub_from_html_skipped: List[str] = []
                                                                epub_from_html_command = build_convert_command(
                                                                    ebook_convert_path,
                                                                    html_output,
                                                                    destination,
                                                                    epub_from_html_config,
                                                                    active_catalog,
                                                                    supported_flags=supported_flags,
                                                                    skipped=epub_from_html_skipped,
                                                                )

                                                                try:
                                                                    epub_from_html_completed = run(
                                                                        epub_from_html_command,
                                                                        capture_output=True,
                                                                        text=True,
                                                                        check=False,
                                                                    )
                                                                except OSError:
                                                                    # Clean up HTML files and raise error
                                                                    _cleanup_pdftohtml_files(html_prefix, destination.parent)
                                                                    raise ConversionError(
                                                                        "ebook-convert falló y todos los reintentos con fallback también fallaron.",
                                                                        command=epub_from_html_command,
                                                                        stdout=stdout,
                                                                        stderr=stderr,
                                                                        returncode=completed.returncode,
                                                                    )

                                                                # Clean up HTML files
                                                                _cleanup_pdftohtml_files(html_prefix, destination.parent)

                                                                # Check if conversion succeeded even with warnings (non-zero exit code)
                                                                if epub_from_html_completed.returncode == 0 or destination.exists():
                                                                    command = epub_from_html_command
                                                                    stdout = epub_from_html_completed.stdout or ""
                                                                    stderr = epub_from_html_completed.stderr or ""
                                                                    for x in fb_skipped:
                                                                        if x not in skipped:
                                                                            skipped.append(x)
                                                                    for x in rx_skipped:
                                                                        if x not in skipped:
                                                                            skipped.append(x)
                                                                    for x in noflow_skipped:
                                                                        if x not in skipped:
                                                                            skipped.append(x)
                                                                    for x in pdftohtml_skipped:
                                                                        if x not in skipped:
                                                                            skipped.append(x)
                                                                    for x in nochapters_skipped:
                                                                        if x not in skipped:
                                                                            skipped.append(x)
                                                                    for x in minimal_skipped:
                                                                        if x not in skipped:
                                                                            skipped.append(x)
                                                                    for x in epub_from_html_skipped:
                                                                        if x not in skipped:
                                                                            skipped.append(x)
                                                                else:
                                                                    combined_stderr = (
                                                                        (stderr or "").rstrip()
                                                                        + "\n\n[Fallback intentado: pdf-header-skip=0, pdf-footer-skip=0]\n"
                                                                        + (fb_completed.stderr or "")
                                                                        + "\n\n[Fallback 2 intentado: regex de cabecera/pie vacíos]\n"
                                                                        + (rx_completed.stderr or "")
                                                                        + "\n\n[Fallback 3 intentado: no-flow=True]\n"
                                                                        + (noflow_completed.stderr or "")
                                                                        + "\n\n[Fallback 4 intentado: pdf-engine=pdftohtml]\n"
                                                                        + (pdftohtml_completed.stderr or "")
                                                                        + "\n\n[Fallback 5 intentado: pdf-engine=pdftohtml + no-chapters]\n"
                                                                        + (nochapters_completed.stderr or "")
                                                                        + "\n\n[Fallback 6 intentado: opciones mínimas + heurísticas desactivadas]\n"
                                                                        + (minimal_completed.stderr or "")
                                                                        + "\n\n[Fallback 7a intentado: conversión PDF->HTML con pdftohtml]\n"
                                                                        + (pdftohtml_completed.stderr or "")
                                                                        + "\n\n[Fallback 7b intentado: conversión HTML->EPUB]\n"
                                                                        + (epub_from_html_completed.stderr or "")
                                                                    )
                                                                    combined_stdout = (
                                                                        (stdout or "").rstrip()
                                                                        + "\n\n[Fallback intentado]\n"
                                                                        + (fb_completed.stdout or "")
                                                                        + "\n\n[Fallback 2 intentado]\n"
                                                                        + (rx_completed.stdout or "")
                                                                        + "\n\n[Fallback 3 intentado]\n"
                                                                        + (noflow_completed.stdout or "")
                                                                        + "\n\n[Fallback 4 intentado]\n"
                                                                        + (pdftohtml_completed.stdout or "")
                                                                        + "\n\n[Fallback 5 intentado]\n"
                                                                        + (nochapters_completed.stdout or "")
                                                                        + "\n\n[Fallback 6 intentado]\n"
                                                                        + (minimal_completed.stdout or "")
                                                                        + "\n\n[Fallback 7a intentado]\n"
                                                                        + (pdftohtml_completed.stdout or "")
                                                                        + "\n\n[Fallback 7b intentado]\n"
                                                                        + (epub_from_html_completed.stdout or "")
                                                                    )
                                                                    raise ConversionError(
                                                                        f"ebook-convert falló (y todos los fallbacks también) con código {epub_from_html_completed.returncode}.",
                                                                        command=epub_from_html_command,
                                                                        stdout=combined_stderr,
                                                                        stderr=combined_stdout,
                                                                        returncode=epub_from_html_completed.returncode,
                                                                    )
                                                            else:
                                                                combined_stderr = (
                                                                    (stderr or "").rstrip()
                                                                    + "\n\n[Fallback intentado: pdf-header-skip=0, pdf-footer-skip=0]\n"
                                                                    + (fb_completed.stderr or "")
                                                                    + "\n\n[Fallback 2 intentado: regex de cabecera/pie vacíos]\n"
                                                                    + (rx_completed.stderr or "")
                                                                    + "\n\n[Fallback 3 intentado: no-flow=True]\n"
                                                                    + (noflow_completed.stderr or "")
                                                                    + "\n\n[Fallback 4 intentado: pdf-engine=pdftohtml]\n"
                                                                    + (pdftohtml_completed.stderr or "")
                                                                    + "\n\n[Fallback 5 intentado: pdf-engine=pdftohtml + no-chapters]\n"
                                                                    + (nochapters_completed.stderr or "")
                                                                    + "\n\n[Fallback 6 intentado: opciones mínimas + heurísticas desactivadas]\n"
                                                                    + (minimal_completed.stderr or "")
                                                                    + "\n\n[Fallback 7a intentado: conversión PDF->HTML con pdftohtml]\n"
                                                                    + (pdftohtml_completed.stderr or "")
                                                                )
                                                                combined_stdout = (
                                                                    (stdout or "").rstrip()
                                                                    + "\n\n[Fallback intentado]\n"
                                                                    + (fb_completed.stdout or "")
                                                                    + "\n\n[Fallback 2 intentado]\n"
                                                                    + (rx_completed.stdout or "")
                                                                    + "\n\n[Fallback 3 intentado]\n"
                                                                    + (noflow_completed.stdout or "")
                                                                    + "\n\n[Fallback 4 intentado]\n"
                                                                    + (pdftohtml_completed.stdout or "")
                                                                    + "\n\n[Fallback 5 intentado]\n"
                                                                    + (nochapters_completed.stdout or "")
                                                                    + "\n\n[Fallback 6 intentado]\n"
                                                                    + (minimal_completed.stdout or "")
                                                                    + "\n\n[Fallback 7a intentado]\n"
                                                                    + (pdftohtml_completed.stdout or "")
                                                                )
                                                                raise ConversionError(
                                                                    f"ebook-convert falló (y todos los fallbacks también) con código {pdftohtml_completed.returncode}.",
                                                                    command=pdftohtml_cmd,
                                                                    stdout=combined_stderr,
                                                                    stderr=combined_stdout,
                                                                    returncode=pdftohtml_completed.returncode,
                                                                )
                                                        except OSError:
                                                            raise ConversionError(
                                                                "ebook-convert falló y todos los reintentos con fallback también fallaron.",
                                                                command=["pdftohtml"],
                                                                stdout=stdout,
                                                                stderr=stderr,
                                                                returncode=completed.returncode,
                                                            )
                                                    else:
                                                        # All fallbacks failed, combine error messages
                                                        combined_stderr = (
                                                        (stderr or "").rstrip()
                                                        + "\n\n[Fallback intentado: pdf-header-skip=0, pdf-footer-skip=0]\n"
                                                        + (fb_completed.stderr or "")
                                                        + "\n\n[Fallback 2 intentado: regex de cabecera/pie vacíos]\n"
                                                        + (rx_completed.stderr or "")
                                                        + "\n\n[Fallback 3 intentado: no-flow=True]\n"
                                                        + (noflow_completed.stderr or "")
                                                        + "\n\n[Fallback 4 intentado: pdf-engine=pdftohtml]\n"
                                                        + (pdftohtml_completed.stderr or "")
                                                        + "\n\n[Fallback 5 intentado: pdf-engine=pdftohtml + no-chapters]\n"
                                                        + (nochapters_completed.stderr or "")
                                                        + "\n\n[Fallback 6 intentado: opciones mínimas + heurísticas desactivadas]\n"
                                                        + (minimal_completed.stderr or "")
                                                    )
                                                    combined_stdout = (
                                                        (stdout or "").rstrip()
                                                        + "\n\n[Fallback intentado]\n"
                                                        + (fb_completed.stdout or "")
                                                        + "\n\n[Fallback 2 intentado]\n"
                                                        + (rx_completed.stdout or "")
                                                        + "\n\n[Fallback 3 intentado]\n"
                                                        + (noflow_completed.stdout or "")
                                                        + "\n\n[Fallback 4 intentado]\n"
                                                        + (pdftohtml_completed.stdout or "")
                                                        + "\n\n[Fallback 5 intentado]\n"
                                                        + (nochapters_completed.stdout or "")
                                                        + "\n\n[Fallback 6 intentado]\n"
                                                        + (minimal_completed.stdout or "")
                                                    )
                                                    raise ConversionError(
                                                        f"ebook-convert falló (y todos los fallbacks también) con código {minimal_completed.returncode}.",
                                                        command=minimal_command,
                                                        stdout=combined_stdout,
                                                        stderr=combined_stderr,
                                                        returncode=minimal_completed.returncode,
                                                    )
                                            else:
                                                # Fallbacks 1-5 failed
                                                combined_stderr = (
                                                    (stderr or "").rstrip()
                                                    + "\n\n[Fallback intentado: pdf-header-skip=0, pdf-footer-skip=0]\n"
                                                    + (fb_completed.stderr or "")
                                                    + "\n\n[Fallback 2 intentado: regex de cabecera/pie vacíos]\n"
                                                    + (rx_completed.stderr or "")
                                                    + "\n\n[Fallback 3 intentado: no-flow=True]\n"
                                                    + (noflow_completed.stderr or "")
                                                    + "\n\n[Fallback 4 intentado: pdf-engine=pdftohtml]\n"
                                                    + (pdftohtml_completed.stderr or "")
                                                    + "\n\n[Fallback 5 intentado: pdf-engine=pdftohtml + no-chapters]\n"
                                                    + (nochapters_completed.stderr or "")
                                                )
                                                combined_stdout = (
                                                    (stdout or "").rstrip()
                                                    + "\n\n[Fallback intentado]\n"
                                                    + (fb_completed.stdout or "")
                                                    + "\n\n[Fallback 2 intentado]\n"
                                                    + (rx_completed.stdout or "")
                                                    + "\n\n[Fallback 3 intentado]\n"
                                                    + (noflow_completed.stdout or "")
                                                    + "\n\n[Fallback 4 intentado]\n"
                                                    + (pdftohtml_completed.stdout or "")
                                                    + "\n\n[Fallback 5 intentado]\n"
                                                    + (nochapters_completed.stdout or "")
                                                )
                                                raise ConversionError(
                                                    f"ebook-convert falló (y los fallbacks 1-5 también) con código {nochapters_completed.returncode}.",
                                                    command=nochapters_command,
                                                    stdout=combined_stdout,
                                                    stderr=combined_stderr,
                                                    returncode=nochapters_completed.returncode,
                                                )
                                    else:
                                        # Fallbacks 1-4 failed
                                        combined_stderr = (
                                            (stderr or "").rstrip()
                                            + "\n\n[Fallback intentado: pdf-header-skip=0, pdf-footer-skip=0]\n"
                                            + (fb_completed.stderr or "")
                                            + "\n\n[Fallback 2 intentado: regex de cabecera/pie vacíos]\n"
                                            + (rx_completed.stderr or "")
                                            + "\n\n[Fallback 3 intentado: no-flow=True]\n"
                                            + (noflow_completed.stderr or "")
                                            + "\n\n[Fallback 4 intentado: pdf-engine=pdftohtml]\n"
                                            + (pdftohtml_completed.stderr or "")
                                        )
                                        combined_stdout = (
                                            (stdout or "").rstrip()
                                            + "\n\n[Fallback intentado]\n"
                                            + (fb_completed.stdout or "")
                                            + "\n\n[Fallback 2 intentado]\n"
                                            + (rx_completed.stdout or "")
                                            + "\n\n[Fallback 3 intentado]\n"
                                            + (noflow_completed.stdout or "")
                                            + "\n\n[Fallback 4 intentado]\n"
                                            + (pdftohtml_completed.stdout or "")
                                        )
                                        raise ConversionError(
                                            f"ebook-convert falló (y los fallbacks 1-4 también) con código {pdftohtml_completed.returncode}.",
                                            command=pdftohtml_command,
                                            stdout=combined_stdout,
                                            stderr=combined_stderr,
                                            returncode=pdftohtml_completed.returncode,
                                        )
                            else:
                                # Fallbacks 1-3 failed
                                combined_stderr = (
                                    (stderr or "").rstrip()
                                    + "\n\n[Fallback intentado: pdf-header-skip=0, pdf-footer-skip=0]\n"
                                    + (fb_completed.stderr or "")
                                    + "\n\n[Fallback 2 intentado: regex de cabecera/pie vacíos]\n"
                                    + (rx_completed.stderr or "")
                                    + "\n\n[Fallback 3 intentado: no-flow=True]\n"
                                    + (noflow_completed.stderr or "")
                                )
                                combined_stdout = (
                                    (stdout or "").rstrip()
                                    + "\n\n[Fallback intentado]\n"
                                    + (fb_completed.stdout or "")
                                    + "\n\n[Fallback 2 intentado]\n"
                                    + (rx_completed.stdout or "")
                                    + "\n\n[Fallback 3 intentado]\n"
                                    + (noflow_completed.stdout or "")
                                )
                                raise ConversionError(
                                    f"ebook-convert falló (y los fallbacks 1, 2 y 3 también) con código {noflow_completed.returncode}.",
                                    command=noflow_command,
                                    stdout=combined_stdout,
                                    stderr=combined_stderr,
                                    returncode=noflow_completed.returncode,
                                )
                                # Fallbacks 1-2 failed
                                combined_stderr = (
                                    (stderr or "").rstrip()
                                    + "\n\n[Fallback intentado: pdf-header-skip=0, pdf-footer-skip=0]\n"
                                    + (fb_completed.stderr or "")
                                    + "\n\n[Fallback 2 intentado: regex de cabecera/pie vacíos]\n"
                                    + (rx_completed.stderr or "")
                                )
                                combined_stdout = (
                                    (stdout or "").rstrip()
                                    + "\n\n[Fallback intentado]\n"
                                    + (fb_completed.stdout or "")
                                    + "\n\n[Fallback 2 intentado]\n"
                                    + (rx_completed.stdout or "")
                                )
                                raise ConversionError(
                                    f"ebook-convert falló (y los fallbacks 1 y 2 también) con código {rx_completed.returncode}.",
                                    command=rx_command,
                                    stdout=combined_stdout,
                                    stderr=combined_stderr,
                                    returncode=rx_completed.returncode,
                                )
                            # Fallback 1 failed
                            combined_stderr = (
                                (stderr or "").rstrip()
                                + "\n\n[Fallback intentado: pdf-header-skip=0, pdf-footer-skip=0]\n"
                                + (fb_completed.stderr or "")
                            )
                            combined_stdout = (
                                (stdout or "").rstrip()
                                + "\n\n[Fallback intentado]\n"
                                + (fb_completed.stdout or "")
                            )
                            raise ConversionError(
                                f"ebook-convert falló (y el fallback también) con código {fb_completed.returncode}.",
                                command=fb_command,
                                stdout=combined_stdout,
                                stderr=combined_stderr,
                                returncode=fb_completed.returncode,
                            )

    # Check if conversion succeeded even with warnings (non-zero exit code)
    if completed.returncode != 0 and not destination.exists():
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
        subset_pdf=oeb_path,  # best-effort reference for downstream use
        stdout=stdout,
        stderr=stderr,
        skipped_options=tuple(skipped),
    )

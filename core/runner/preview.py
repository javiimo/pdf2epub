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
from core.runner.pdf_preprocessor import (
    PreprocessError,
    PreprocessResult,
    options_from_extras,
    preprocess_pdf,
)
from core.runner.pdf_subset import PdfSubsetError, prepare_pdf_subset
from core.runner.temp_manager import TemporaryWorkspace
from core.presets import apply_preset, get_presets

__all__ = ["PreviewError", "PreviewResult", "run_preview"]


def _ensure_catalog(catalog: Optional[Catalog]) -> Catalog:
    return catalog if catalog is not None else get_catalog()


def _is_header_footer_detection_bug(stderr: str) -> bool:
    """Heuristic to detect calibre PDF reflow header/footer bug.

    We look for the signature of an IndexError happening inside
    ``find_header_footer`` from ``calibre/ebooks/pdf/reflow.py``.
    """
    if not stderr:
        return False
    s = stderr.lower()
    return ("find_header_footer" in s) and ("indexerror" in s)


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
    preprocess: Optional[PreprocessResult] = None

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
    """Execute ebook-convert to generate an OEB preview for given config."""
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
            # Work on a shallow copy of config so we don't mutate caller state
            cfg_options = dict(config.options)
            apply_preset(cfg_options, kobo)
            config = replace(config, options=cfg_options)
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
    except (PdfSubsetError, PreprocessError) as exc:
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

    # Fallback: calibre PDF reflow header/footer autodetection can crash with
    # IndexError on some PDFs/ranges. Retry disabling auto-skip by forcing
    # header/footer skip to 0 when we detect that signature.
    if completed.returncode != 0 and _is_header_footer_detection_bug(stderr):
        # Prepare a modified config overriding only the two relevant options.
        fallback_options = dict(config.options)
        fallback_options["pdf-header-skip"] = "0"
        fallback_options["pdf-footer-skip"] = "0"
        fallback_config = replace(config, options=fallback_options)

        fb_skipped: List[str] = []
        fallback_command = build_convert_command(
            ebook_convert_path,
            subset_pdf,
            oeb_output,
            fallback_config,
            active_catalog,
            supported_flags=supported_flags,
            skipped=fb_skipped,
        )

        try:
            fb_completed = run(
                fallback_command,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            # If the second attempt cannot even be executed, keep the original error.
            workspace.cleanup()
            raise PreviewError(
                f"ebook-convert falló y el reintento con fallback también falló.",
                command=fallback_command,
                stdout=stdout,
                stderr=stderr,
                returncode=completed.returncode,
            )

        if fb_completed.returncode == 0:
            # Use fallback run results from here on.
            command = fallback_command
            stdout = fb_completed.stdout or ""
            stderr = fb_completed.stderr or ""
            # Merge skipped lists, keeping order.
            skipped.extend(x for x in fb_skipped if x not in skipped)
        else:
            # Try a second-level fallback: force regexes that never match to
            # avoid any header/footer stripping code paths in older calibre.
            if _is_header_footer_detection_bug(fb_completed.stderr or ""):
                regex_options = dict(fallback_config.options)
                regex_options.setdefault("pdf-header-skip", "0")
                regex_options.setdefault("pdf-footer-skip", "0")
                regex_options["pdf-header-regex"] = "(?!)"
                regex_options["pdf-footer-regex"] = "(?!)"
                regex_config = replace(fallback_config, options=regex_options)

                rx_skipped: List[str] = []
                regex_command = build_convert_command(
                    ebook_convert_path,
                    subset_pdf,
                    oeb_output,
                    regex_config,
                    active_catalog,
                    supported_flags=supported_flags,
                    skipped=rx_skipped,
                )

                try:
                    rx_completed = run(
                        regex_command,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                except OSError:
                    workspace.cleanup()
                    raise PreviewError(
                        "ebook-convert falló y los reintentos con fallback también fallaron.",
                        command=regex_command,
                        stdout=stdout,
                        stderr=stderr,
                        returncode=completed.returncode,
                    )

                if rx_completed.returncode == 0:
                    command = regex_command
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
                        noflow_options = dict(regex_config.options)
                        noflow_options.setdefault("pdf-header-skip", "0")
                        noflow_options.setdefault("pdf-footer-skip", "0")
                        noflow_options.setdefault("pdf-header-regex", "(?!)")
                        noflow_options.setdefault("pdf-footer-regex", "(?!)")
                        # Try to disable the reflow engine completely
                        noflow_options["no-flow"] = True
                        noflow_config = replace(regex_config, options=noflow_options)

                        noflow_skipped: List[str] = []
                        noflow_command = build_convert_command(
                            ebook_convert_path,
                            subset_pdf,
                            oeb_output,
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
                            workspace.cleanup()
                            raise PreviewError(
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
                                    oeb_output,
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
                                    workspace.cleanup()
                                    raise PreviewError(
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
                                            oeb_output,
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
                                            workspace.cleanup()
                                            raise PreviewError(
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
                                                    oeb_output,
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
                                                    workspace.cleanup()
                                                    raise PreviewError(
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
                                                    # All fallbacks failed, include all runs' info in error to aid debugging
                                                    workspace.cleanup()
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
                                                    raise PreviewError(
                                                        f"ebook-convert falló (y todos los fallbacks también) con código {minimal_completed.returncode}.",
                                                        command=minimal_command,
                                                        stdout=combined_stdout,
                                                        stderr=combined_stderr,
                                                        returncode=minimal_completed.returncode,
                                                    )
                                    else:
                                        # Fallbacks 1-4 failed
                                        workspace.cleanup()
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
                                        raise PreviewError(
                                            f"ebook-convert falló (y los fallbacks 1-4 también) con código {pdftohtml_completed.returncode}.",
                                            command=pdftohtml_command,
                                            stdout=combined_stdout,
                                            stderr=combined_stderr,
                                            returncode=pdftohtml_completed.returncode,
                                        )
                            else:
                                    # Fallbacks 1-3 failed
                                    workspace.cleanup()
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
                                    raise PreviewError(
                                        f"ebook-convert falló (y los fallbacks 1, 2 y 3 también) con código {noflow_completed.returncode}.",
                                        command=noflow_command,
                                        stdout=combined_stdout,
                                        stderr=combined_stderr,
                                        returncode=noflow_completed.returncode,
                                    )
                    else:
                        # Fallbacks 1-2 failed
                        workspace.cleanup()
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
                        raise PreviewError(
                            f"ebook-convert falló (y los fallbacks 1 y 2 también) con código {rx_completed.returncode}.",
                            command=regex_command,
                            stdout=combined_stdout,
                            stderr=combined_stderr,
                            returncode=rx_completed.returncode,
                        )
            else:
                # Include both runs' info in error to aid debugging.
                workspace.cleanup()
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
                raise PreviewError(
                    f"ebook-convert falló (y el fallback también) con código {fb_completed.returncode}.",
                    command=fallback_command,
                    stdout=combined_stdout,
                    stderr=combined_stderr,
                    returncode=fb_completed.returncode,
                )

    if completed.returncode != 0 and not _is_header_footer_detection_bug(stderr):
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
        preprocess=preprocess_result,
    )

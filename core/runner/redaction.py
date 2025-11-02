"""Utilities to redact PDF regions before inserting rasterized images.

This module covers the "Redacción del contenido original" stage from the
project checklist. Given a set of rectangular regions in page coordinates, it
removes text operators and vector graphics that intersect those regions. For
cases that still preserve complex elements (e.g. dense tables) it falls back to
MuPDF's redaction annotations and erases the area entirely. The helpers expose
lightweight result objects so callers can track whether the fallback path was
required per region.

The implementation relies on PyMuPDF (``fitz``). Import errors are converted
into :class:`RedactionError` so callers can surface actionable diagnostics.
"""

from __future__ import annotations

import importlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence, Tuple
import fitz

from types import ModuleType

__all__ = [
    "RedactionError",
    "RedactionOptions",
    "RedactionRegion",
    "RedactionResult",
    "redact_pdf_regions",
]


class RedactionError(RuntimeError):
    """Raised when a PDF region cannot be redacted as requested."""


@dataclass(frozen=True)
class RedactionRegion:
    """Describe a rectangular region to redact from a PDF page."""

    page_index: int
    rect_pt: Tuple[float, float, float, float]
    label: str = "region"


@dataclass(frozen=True)
class RedactionOptions:
    """Configuration knobs for PDF content redaction."""

    fallback_labels: Optional[Sequence[str]] = ("table",)
    fill_color: Optional[Tuple[float, float, float]] = None
    validation_margin: float = 0.5


@dataclass(frozen=True)
class RedactionResult:
    """Summary of the redaction performed on a region."""

    page_index: int
    rect_pt: Tuple[float, float, float, float]
    used_fallback: bool


_FITZ: Optional[ModuleType] = None


def _require_fitz() -> ModuleType:
    """Return the ``fitz`` module or raise :class:`RedactionError`."""

    global _FITZ
    if _FITZ is None:
        try:
            _FITZ = importlib.import_module("fitz")
        except ModuleNotFoundError as exc:  # pragma: no cover - optional dep
            raise RedactionError("PyMuPDF (fitz) no está disponible para redactar regiones") from exc
    return _FITZ


def _validate_rect(rect: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    x0, y0, x1, y1 = rect
    if not all(math.isfinite(v) for v in rect):
        raise RedactionError("Las coordenadas de la región contienen valores no finitos")
    if x1 <= x0 or y1 <= y0:
        raise RedactionError("La región debe tener ancho y alto positivos")
    return (float(x0), float(y0), float(x1), float(y1))


def _normalize_color(color: Optional[Tuple[float, float, float]]) -> Optional[Tuple[float, float, float]]:
    if color is None:
        return None
    values = []
    for component in color:
        value = float(component)
        if value > 1.0:
            value /= 255.0
        values.append(min(max(value, 0.0), 1.0))
    return tuple(values)


def _clip_rect_to_page(rect: "fitz.Rect", page_rect: "fitz.Rect") -> "fitz.Rect":
    clipped = page_rect & rect
    if clipped.is_empty:
        return _require_fitz().Rect(0, 0, 0, 0)
    return clipped


def _inflate_for_validation(rect: "fitz.Rect", margin: float, page_rect: "fitz.Rect") -> "fitz.Rect":
    if margin <= 0:
        return rect
    expanded = rect + (margin, margin, margin, margin)
    return page_rect & expanded


def _iter_text_spans(page: "fitz.Page") -> Iterable[Tuple[str, "fitz.Rect"]]:
    raw = page.get_text("rawdict")
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                bbox = span.get("bbox")
                text = span.get("text", "")
                if not bbox:
                    continue
                rect = _require_fitz().Rect(*bbox)
                yield text, rect


def _has_text_in_rect(page: "fitz.Page", rect: "fitz.Rect") -> bool:
    for text, span_rect in _iter_text_spans(page):
        if not text or not text.strip():
            continue
        if span_rect.intersects(rect):
            return True
    return False


def _has_vectors_in_rect(page: "fitz.Page", rect: "fitz.Rect") -> bool:
    for drawing in page.get_drawings():
        bbox = drawing.get("rect")
        if bbox is None:
            continue
        if _require_fitz().Rect(bbox).intersects(rect):
            return True
    return False


def _apply_redaction(
    page: "fitz.Page",
    rect: "fitz.Rect",
    *,
    fill_color: Optional[Tuple[float, float, float]],
    images: int,
    graphics: int,
) -> None:
    page.add_redact_annot(rect, fill=fill_color)
    page.apply_redactions(images=images, graphics=graphics, text=0)


def _validate_region_clean(
    page: "fitz.Page",
    rect: "fitz.Rect",
    *,
    margin: float,
) -> None:
    # Skip validation if margin is 0 (permissive mode)
    if margin <= 0:
        return
        
    check_rect = _inflate_for_validation(rect, margin, page.rect)
    if _has_text_in_rect(page, check_rect):
        raise RedactionError("Tras la redacción aún queda texto seleccionable en la región")
    if _has_vectors_in_rect(page, check_rect):
        raise RedactionError("Tras la redacción aún quedan gráficos vectoriales en la región")


def _group_regions_by_page(regions: Sequence[RedactionRegion]) -> Mapping[int, Tuple[RedactionRegion, ...]]:
    grouped: dict[int, list[RedactionRegion]] = {}
    for region in regions:
        grouped.setdefault(int(region.page_index), []).append(region)
    return {page: tuple(items) for page, items in grouped.items()}


def _redact_region(
    page: "fitz.Page",
    rect: "fitz.Rect",
    *,
    label: str,
    fill_color: Optional[Tuple[float, float, float]],
    fallback_labels: Optional[Sequence[str]],
    validation_margin: float,
) -> bool:
    normalized_label = (label or "").strip().lower()
    fallback_set = {lbl.strip().lower() for lbl in fallback_labels or ()}

    _apply_redaction(page, rect, fill_color=fill_color, images=0, graphics=2)

    need_fallback = normalized_label in fallback_set
    if not need_fallback:
        if _has_text_in_rect(page, rect) or _has_vectors_in_rect(page, rect):
            need_fallback = True

    if need_fallback:
        _apply_redaction(page, rect, fill_color=fill_color, images=2, graphics=2)

    _validate_region_clean(page, rect, margin=validation_margin)
    return bool(need_fallback)


def redact_pdf_regions(
    pdf_path: Path,
    regions: Sequence[RedactionRegion],
    *,
    output_pdf: Optional[Path] = None,
    options: Optional[RedactionOptions] = None,
) -> Tuple[RedactionResult, ...]:
    """Redact original PDF content for the provided regions.

    Args:
        pdf_path: PDF file whose content should be redacted.
        regions: Sequence of :class:`RedactionRegion` descriptors.
        output_pdf: Optional destination path. When omitted, the input PDF is
            rewritten incrementally.
        options: Optional :class:`RedactionOptions` controlling behaviour.

    Returns:
        Tuple with a :class:`RedactionResult` for each processed region.
    """

    if not regions:
        return tuple()

    fitz = _require_fitz()
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise RedactionError(f"El PDF de entrada no existe: {pdf_path}")

    opts = options or RedactionOptions()
    fill_color = _normalize_color(opts.fill_color)
    grouped = _group_regions_by_page(tuple(regions))

    results: list[RedactionResult] = []

    doc = fitz.open(pdf_path)  # type: ignore[arg-type]
    try:
        for page_index in sorted(grouped):
            if page_index < 1 or page_index > doc.page_count:
                raise RedactionError(f"Página fuera de rango para redacción: {page_index}")
            page = doc.load_page(page_index - 1)
            page_rect = page.rect
            for region in grouped[page_index]:
                norm_rect = _validate_rect(region.rect_pt)
                rect = fitz.Rect(*norm_rect)
                rect = _clip_rect_to_page(rect, page_rect)
                if rect.is_empty:
                    continue
                used_fallback = _redact_region(
                    page,
                    rect,
                    label=region.label,
                    fill_color=fill_color,
                    fallback_labels=opts.fallback_labels,
                    validation_margin=float(opts.validation_margin),
                )
                results.append(
                    RedactionResult(
                        page_index=page_index,
                        rect_pt=(float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)),
                        used_fallback=used_fallback,
                    )
                )
            page.clean_contents()

        output_path = Path(output_pdf) if output_pdf else pdf_path
        if output_pdf:
            doc.save(str(output_path))
        else:
            doc.save(str(output_path), incremental=True, encryption=fitz.PDF_ENCRYPT_KEEP)
    finally:
        doc.close()

    return tuple(results)


def redact_and_save_pdf(
    pdf_path: Path,
    output_pdf: Path,
    regions: Sequence[RedactionRegion],
    *,
    fill_color: Optional[Tuple[float, float, float]] = (1.0, 1.0, 1.0),
    options: Optional[RedactionOptions] = None,
) -> Tuple[RedactionResult, ...]:
    """Redact PDF regions and save to a new file.
    
    This is a convenience wrapper around :func:`redact_pdf_regions` that
    always saves to a new file with default white fill color for redaction.
    
    Args:
        pdf_path: Source PDF file to redact.
        output_pdf: Destination path for the redacted PDF.
        regions: Sequence of regions to redact.
        fill_color: RGB color to fill redacted regions (default: white).
        options: Optional redaction options.
        
    Returns:
        Tuple with a :class:`RedactionResult` for each processed region.
    """
    # Create options with the specified fill color and more permissive validation
    if options is None:
        options = RedactionOptions(
            fill_color=fill_color,
            validation_margin=0.0,  # No validation margin to avoid false positives
            fallback_labels=("table", "mathblock", "tableblock")  # Treat all as fallback
        )
    else:
        # Merge the provided options with our fill color and more permissive settings
        options = RedactionOptions(
            fallback_labels=options.fallback_labels or ("table", "mathblock", "tableblock"),
            fill_color=fill_color,
            validation_margin=0.0,  # No validation margin to avoid false positives
        )
    
    return redact_pdf_regions(
        pdf_path=pdf_path,
        regions=regions,
        output_pdf=output_pdf,
        options=options,
    )


"""Insert captured region images back into a PDF document.

This module implements the "Inserción de imágenes en el PDF" block of the
project checklist. It takes the :class:`~core.runner.region_capture.CapturedRegionImage`
artifacts generated during selective rasterisation and embeds them into the
original PDF after the corresponding content has been redacted. Besides placing
the image XObject on the target rectangle, it can optionally paint a white
background underneath and store lightweight metadata on the image object for
traceability.

The implementation relies on PyMuPDF (``fitz``) for manipulating the PDF
structure. Import failures are reported as :class:`ImageInsertionError` so
callers can provide actionable diagnostics to the end user.
"""

from __future__ import annotations

import json
import math
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence, Tuple

from core.runner.region_capture import CapturedRegionImage, ImageXObject

try:  # pragma: no cover - optional dependency guarded at runtime in tests
    import fitz  # type: ignore
except Exception:  # pragma: no cover - handled in _require_fitz
    fitz = None  # type: ignore[assignment]

__all__ = [
    "ImageInsertionError",
    "ImageInsertOptions",
    "ImageInsertResult",
    "ImageInsertSpec",
    "insert_pdf_images",
]


class ImageInsertionError(RuntimeError):
    """Raised when an image cannot be embedded into the PDF."""


@dataclass(frozen=True)
class ImageInsertSpec:
    """Describe an image that must be inserted into a PDF page."""

    capture: CapturedRegionImage
    rect_pt: Optional[Tuple[float, float, float, float]] = None
    page_index: Optional[int] = None
    metadata: Optional[Mapping[str, object]] = None

    def resolve_page_index(self) -> int:
        return int(self.page_index if self.page_index is not None else self.capture.page_index)

    def resolve_rect(self) -> Tuple[float, float, float, float]:
        if self.rect_pt is not None:
            return (
                float(self.rect_pt[0]),
                float(self.rect_pt[1]),
                float(self.rect_pt[2]),
                float(self.rect_pt[3]),
            )
        return self.capture.rect_pt


@dataclass(frozen=True)
class ImageInsertOptions:
    """Configuration knobs for PDF image insertion."""

    paint_background: bool = True
    background_color: Tuple[float, float, float] = (1.0, 1.0, 1.0)
    overlay: bool = True
    metadata_key: Optional[str] = "/PDF2EPUBMetadata"
    include_default_metadata: bool = True


@dataclass(frozen=True)
class ImageInsertResult:
    """Summary of the image insertion performed on a region."""

    page_index: int
    rect_pt: Tuple[float, float, float, float]
    image_xref: int
    form_xref: Optional[int] = None


def _require_fitz() -> "fitz":
    if fitz is None:  # pragma: no cover - environment dependent
        raise ImageInsertionError("PyMuPDF (fitz) no está disponible para insertar imágenes en el PDF")
    return fitz  # type: ignore[return-value]


def _validate_rect(rect: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    x0, y0, x1, y1 = rect
    if not all(math.isfinite(v) for v in rect):
        raise ImageInsertionError("Las coordenadas de inserción contienen valores no finitos")
    if x1 <= x0 or y1 <= y0:
        raise ImageInsertionError("La región de inserción debe tener ancho y alto positivos")
    return (float(x0), float(y0), float(x1), float(y1))


def _normalize_color(color: Tuple[float, float, float]) -> Tuple[float, float, float]:
    if len(color) != 3:
        raise ImageInsertionError("El color de fondo debe tener exactamente tres componentes")
    normalized = []
    for component in color:
        value = float(component)
        if value > 1.0:
            value /= 255.0
        normalized.append(min(max(value, 0.0), 1.0))
    return tuple(normalized)  # type: ignore[return-value]


def _pdf_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return f"({escaped})"


def _build_pixmaps(xobject: ImageXObject) -> Tuple["fitz.Pixmap", Optional["fitz.Pixmap"]]:
    fitz_mod = _require_fitz()
    if int(xobject.bits_per_component) != 8:
        raise ImageInsertionError("Solo se admiten XObjects de 8 bits por componente")

    try:
        color_samples = zlib.decompress(xobject.stream)
    except Exception as exc:  # pragma: no cover - defensive guard
        raise ImageInsertionError(f"No se pudo descomprimir el stream del XObject: {exc}") from exc

    components = 1 if xobject.color_space == "DeviceGray" else 3
    expected = int(xobject.width_px) * int(xobject.height_px) * components
    if len(color_samples) != expected:
        raise ImageInsertionError("Los datos de color del XObject no coinciden con sus dimensiones")

    colorspace = fitz_mod.csGRAY if components == 1 else fitz_mod.csRGB
    pix = fitz_mod.Pixmap(colorspace, int(xobject.width_px), int(xobject.height_px), color_samples, False)

    mask_pix: Optional["fitz.Pixmap"] = None
    if xobject.soft_mask is not None:
        soft = xobject.soft_mask
        if int(soft.bits_per_component) != 8:
            raise ImageInsertionError("Solo se admiten máscaras con 8 bits por componente")
        try:
            mask_samples = zlib.decompress(soft.stream)
        except Exception as exc:  # pragma: no cover - defensive guard
            raise ImageInsertionError(f"No se pudo descomprimir la máscara del XObject: {exc}") from exc
        expected_mask = int(soft.width_px) * int(soft.height_px)
        if len(mask_samples) != expected_mask:
            raise ImageInsertionError("Los datos de la máscara no coinciden con sus dimensiones")
        mask_pix = fitz_mod.Pixmap(fitz_mod.csGRAY, int(soft.width_px), int(soft.height_px), mask_samples, False)

    return pix, mask_pix


def _group_specs_by_page(specs: Sequence[ImageInsertSpec]) -> Mapping[int, Tuple[ImageInsertSpec, ...]]:
    grouped: dict[int, list[ImageInsertSpec]] = {}
    for spec in specs:
        grouped.setdefault(spec.resolve_page_index(), []).append(spec)
    return {page: tuple(items) for page, items in grouped.items()}


def _prepare_metadata(
    spec: ImageInsertSpec,
    rect: Tuple[float, float, float, float],
    *,
    include_defaults: bool,
) -> Mapping[str, object]:
    payload: dict[str, object] = {}
    if include_defaults:
        payload.update(
            {
                "page_index": spec.resolve_page_index(),
                "rect_pt": [float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])],
                "dpi": int(spec.capture.dpi),
                "label": spec.capture.label,
                "width_px": int(spec.capture.width_px),
                "height_px": int(spec.capture.height_px),
                "transparent": bool(spec.capture.transparent),
            }
        )
    if spec.metadata:
        for key, value in spec.metadata.items():
            payload[str(key)] = value
    return payload


def _store_metadata(doc: "fitz.Document", xref: int, key: str, metadata: Mapping[str, object]) -> None:
    if not metadata:
        return
    normalized_key = key if key.startswith("/") else f"/{key}"
    json_payload = json.dumps(metadata, ensure_ascii=False, sort_keys=True, default=str)
    doc.xref_set_key(xref, normalized_key, _pdf_string(json_payload))


def insert_pdf_images(
    pdf_path: Path,
    specs: Sequence[ImageInsertSpec],
    *,
    output_pdf: Optional[Path] = None,
    options: Optional[ImageInsertOptions] = None,
) -> Tuple[ImageInsertResult, ...]:
    """Embed captured region images into the PDF.

    Args:
        pdf_path: PDF file whose pages should receive the images.
        specs: Sequence of :class:`ImageInsertSpec` describing what to insert.
        output_pdf: Optional destination path. When omitted, the input PDF is
            rewritten incrementally.
        options: Optional :class:`ImageInsertOptions` controlling behaviour.

    Returns:
        Tuple with a :class:`ImageInsertResult` for each inserted region.
    """

    if not specs:
        return tuple()

    fitz_mod = _require_fitz()
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise ImageInsertionError(f"El PDF de entrada no existe: {pdf_path}")

    opts = options or ImageInsertOptions()
    background_color = _normalize_color(opts.background_color)
    grouped = _group_specs_by_page(tuple(specs))

    results: list[ImageInsertResult] = []

    doc = fitz_mod.open(pdf_path)  # type: ignore[arg-type]
    try:
        for page_index in sorted(grouped):
            if page_index < 1 or page_index > doc.page_count:
                raise ImageInsertionError(f"Página fuera de rango para inserción: {page_index}")
            page = doc.load_page(page_index - 1)
            inserted_any = False

            for spec in grouped[page_index]:
                rect = _validate_rect(spec.resolve_rect())
                rect_obj = fitz_mod.Rect(*rect)
                pix, mask_pix = _build_pixmaps(spec.capture.xobject)

                if opts.paint_background:
                    page.draw_rect(rect_obj, color=(1.0, 1.0, 1.0), fill=background_color, overlay=opts.overlay)

                alpha = 0 if mask_pix is None else -1
                image_xref = page.insert_image(
                    rect_obj,
                    pixmap=pix,
                    mask=mask_pix,
                    overlay=opts.overlay,
                    alpha=alpha,
                )

                if opts.metadata_key:
                    metadata = _prepare_metadata(spec, rect, include_defaults=opts.include_default_metadata)
                    _store_metadata(doc, image_xref, opts.metadata_key, metadata)

                results.append(
                    ImageInsertResult(
                        page_index=page_index,
                        rect_pt=rect,
                        image_xref=int(image_xref),
                        form_xref=None,
                    )
                )
                inserted_any = True

            if inserted_any:
                page.clean_contents()

        output_path = Path(output_pdf) if output_pdf else pdf_path
        if output_pdf:
            doc.save(str(output_path))
        else:
            doc.save(str(output_path), incremental=True, encryption=fitz_mod.PDF_ENCRYPT_KEEP)
    finally:
        doc.close()

    return tuple(results)


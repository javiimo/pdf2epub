"""Utilities to rasterize PDF regions and build PDF image XObjects.

This module focuses on the "Captura de imagen por región" stage of the
checklist. It provides helpers to render a rectangular area from a PDF page
into a bitmap at a controlled DPI, generate an image XObject representation
ready for PDF insertion, and persist both the bitmap and metadata on disk.

The implementation relies on PyMuPDF (``fitz``) for high-quality rendering
and performs light processing on the resulting pixmaps to expose their raw
samples. Transparency can be preserved by emitting a soft mask XObject.
"""

from __future__ import annotations

import json
import math
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Tuple

try:  # pragma: no cover - optional dependency guarded at runtime in tests
    import fitz  # type: ignore
except Exception:  # pragma: no cover - handled via _require_fitz
    fitz = None  # type: ignore[assignment]

__all__ = [
    "CaptureOptions",
    "CapturedRegionImage",
    "ImageXObject",
    "RegionCaptureError",
    "RegionSpec",
    "capture_pdf_regions",
]


class RegionCaptureError(RuntimeError):
    """Raised when a PDF region cannot be rendered or processed."""


@dataclass(frozen=True)
class RegionSpec:
    """Describe a rectangular region to capture from a PDF page."""

    page_index: int
    rect_pt: Tuple[float, float, float, float]
    label: str = "region"


@dataclass(frozen=True)
class ImageXObject:
    """Lightweight representation of a PDF image XObject."""

    width_px: int
    height_px: int
    color_space: str
    bits_per_component: int
    stream: bytes
    filter: str = "FlateDecode"
    decode_parms: Optional[dict[str, int]] = None
    soft_mask: Optional["ImageXObject"] = None

    def as_pdf_dict(self) -> dict[str, object]:
        """Return a dictionary mirroring the PDF image XObject entries."""

        base: dict[str, object] = {
            "/Type": "/XObject",
            "/Subtype": "/Image",
            "/Width": int(self.width_px),
            "/Height": int(self.height_px),
            "/ColorSpace": f"/{self.color_space}",
            "/BitsPerComponent": int(self.bits_per_component),
            "/Filter": f"/{self.filter}",
        }
        if self.decode_parms:
            base["/DecodeParms"] = {k: int(v) for k, v in self.decode_parms.items()}
        if self.soft_mask is not None:
            # Soft mask must be referenced separately; include placeholder dict.
            base["/SMask"] = self.soft_mask.as_pdf_dict()
        return base


@dataclass(frozen=True)
class CapturedRegionImage:
    """Result of rendering a PDF region to disk."""

    image_path: Path
    metadata_path: Path
    page_index: int
    rect_pt: Tuple[float, float, float, float]
    dpi: int
    label: str
    width_px: int
    height_px: int
    transparent: bool
    xobject: ImageXObject

    @property
    def metadata(self) -> dict[str, object]:
        """Load metadata JSON associated to this capture."""

        try:
            return json.loads(self.metadata_path.read_text(encoding="utf-8"))
        except Exception as exc:  # pragma: no cover - defensive fallback
            raise RegionCaptureError(f"No se pudo leer metadatos de {self.metadata_path}: {exc}") from exc


@dataclass(frozen=True)
class CaptureOptions:
    """Configuration knobs for region capture."""

    dpi: float = 360.0
    transparent: bool = False
    image_prefix: str = "region"


def _require_fitz() -> None:
    if fitz is None:  # pragma: no cover - environment dependent
        raise RegionCaptureError("PyMuPDF (fitz) no disponible para rasterizar regiones del PDF")


def _validate_dpi(dpi: float) -> float:
    value = float(dpi)
    if value < 300.0 or value > 600.0:
        raise RegionCaptureError("El DPI para captura selectiva debe estar entre 300 y 600")
    return value


def _validate_rect(rect: Tuple[float, float, float, float]) -> Tuple[float, float, float, float]:
    x0, y0, x1, y1 = rect
    if not all(math.isfinite(v) for v in rect):
        raise RegionCaptureError("Las coordenadas de la región contienen valores no finitos")
    if x1 <= x0 or y1 <= y0:
        raise RegionCaptureError("La región debe tener ancho y alto positivos")
    return (float(x0), float(y0), float(x1), float(y1))


def _compute_pixel_size(rect: Tuple[float, float, float, float], dpi: float) -> Tuple[int, int]:
    x0, y0, x1, y1 = rect
    width_pt = max(0.0, x1 - x0)
    height_pt = max(0.0, y1 - y0)
    width_px = max(1, int(round(width_pt * dpi / 72.0)))
    height_px = max(1, int(round(height_pt * dpi / 72.0)))
    return width_px, height_px


def _split_samples(pix: "fitz.Pixmap") -> tuple[bytes, Optional[bytes], int]:
    """Return (color_samples, alpha_samples, components)."""

    total_components = int(getattr(pix, "n", 0))
    has_alpha = bool(getattr(pix, "alpha", 0))
    if has_alpha:
        if total_components <= 1:
            raise RegionCaptureError("Pixmap con alfa pero sin componentes de color")
        components = total_components - 1
    else:
        components = total_components
    total_components = components + (1 if has_alpha else 0)
    samples = pix.samples
    if total_components <= 0:
        raise RegionCaptureError("Pixmap sin componentes de color")

    if not has_alpha:
        return bytes(samples), None, components

    total_pixels = pix.width * pix.height
    color = bytearray(total_pixels * components)
    alpha = bytearray(total_pixels)
    mv = memoryview(samples)
    comp = components
    stride_raw = getattr(pix, "stride", None)
    try:
        stride = int(stride_raw) if stride_raw is not None else int(pix.width) * total_components
    except (TypeError, ValueError):  # pragma: no cover - defensive
        stride = int(pix.width) * total_components
    if stride <= 0:
        stride = int(pix.width) * total_components

    c_idx = 0
    a_idx = 0
    for row in range(int(pix.height)):
        row_start = row * stride
        row_end = row_start + int(pix.width) * total_components
        if row_end > len(mv):  # pragma: no cover - defensive guard
            raise RegionCaptureError("Datos incompletos al separar canales del pixmap")
        row_view = mv[row_start:row_end]
        for col in range(int(pix.width)):
            offset = col * total_components
            color[c_idx : c_idx + comp] = row_view[offset : offset + comp]
            c_idx += comp
            alpha[a_idx] = row_view[offset + comp]
            a_idx += 1

    return bytes(color), bytes(alpha), components


def _ensure_colorspace(pix: "fitz.Pixmap") -> "fitz.Pixmap":
    """Convert pixmap to grayscale or RGB if needed."""

    cs = getattr(pix, "colorspace", None)
    components = int(getattr(cs, "n", 0)) if cs is not None else int(getattr(pix, "n", 0))
    if components in (0, 1, 3):
        return pix

    target = fitz.csRGB if components != 1 else fitz.csGRAY
    return fitz.Pixmap(target, pix)


def _pixmap_to_xobject(pix: "fitz.Pixmap", *, transparent: bool) -> ImageXObject:
    pix = _ensure_colorspace(pix)
    color_samples, alpha_samples, components = _split_samples(pix)
    if components not in (1, 3):
        raise RegionCaptureError(f"Color space no soportado para XObject ({components} canales)")

    color_space = "DeviceGray" if components == 1 else "DeviceRGB"
    stream = zlib.compress(color_samples)
    decode_parms = {
        "Columns": int(pix.width),
        "BitsPerComponent": 8,
        "Colors": int(components),
    }
    base = ImageXObject(
        width_px=int(pix.width),
        height_px=int(pix.height),
        color_space=color_space,
        bits_per_component=8,
        stream=stream,
        decode_parms=decode_parms,
    )

    if transparent and alpha_samples is not None:
        mask_stream = zlib.compress(alpha_samples)
        soft_mask = ImageXObject(
            width_px=int(pix.width),
            height_px=int(pix.height),
            color_space="DeviceGray",
            bits_per_component=8,
            stream=mask_stream,
            decode_parms={
                "Columns": int(pix.width),
                "BitsPerComponent": 8,
                "Colors": 1,
            },
        )
        return ImageXObject(
            width_px=base.width_px,
            height_px=base.height_px,
            color_space=base.color_space,
            bits_per_component=base.bits_per_component,
            stream=base.stream,
            filter=base.filter,
            decode_parms=base.decode_parms,
            soft_mask=soft_mask,
        )

    return base


def _render_region_pixmap(
    doc: "fitz.Document",
    page_index: int,
    rect_pt: Tuple[float, float, float, float],
    *,
    dpi: float,
    transparent: bool,
) -> "fitz.Pixmap":
    page_count = int(getattr(doc, "page_count", 0))
    if page_index < 1 or page_index > page_count:
        raise RegionCaptureError(f"Página fuera de rango: {page_index}")

    rect = fitz.Rect(*rect_pt)
    page = doc.load_page(page_index - 1)
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    pix = page.get_pixmap(matrix=matrix, clip=rect, alpha=bool(transparent))
    return pix


def capture_pdf_regions(
    pdf_path: Path,
    regions: Sequence[RegionSpec],
    *,
    output_dir: Path,
    options: Optional[CaptureOptions] = None,
) -> Tuple[CapturedRegionImage, ...]:
    """Render PDF regions to PNG images with metadata and XObjects."""

    if not regions:
        return tuple()

    _require_fitz()
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise RegionCaptureError(f"El PDF de entrada no existe: {pdf_path}")

    opts = options or CaptureOptions()
    dpi_value = _validate_dpi(float(opts.dpi))
    transparent = bool(opts.transparent)
    prefix = str(opts.image_prefix or "region").strip() or "region"

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    captures: list[CapturedRegionImage] = []

    with fitz.open(pdf_path) as doc:  # type: ignore[arg-type]
        for idx, spec in enumerate(regions, start=1):
            rect = _validate_rect(spec.rect_pt)
            expected_w, expected_h = _compute_pixel_size(rect, dpi_value)
            pix = _render_region_pixmap(
                doc,
                int(spec.page_index),
                rect,
                dpi=dpi_value,
                transparent=transparent,
            )

            width_px = int(pix.width)
            height_px = int(pix.height)
            target_width = max(1, int(expected_w))
            target_height = max(1, int(expected_h))
            if width_px != target_width or height_px != target_height:
                total_components = int(getattr(pix, "n", 0))
                if total_components <= 0:
                    raise RegionCaptureError("Pixmap sin componentes de color")

                stride_raw = getattr(pix, "stride", None)
                try:
                    stride = int(stride_raw) if stride_raw is not None else width_px * total_components
                except (TypeError, ValueError):  # pragma: no cover - defensive
                    stride = width_px * total_components
                if stride <= 0:
                    stride = width_px * total_components

                matrix = fitz.Matrix(dpi_value / 72.0, dpi_value / 72.0)
                device_rect = fitz.Rect(*rect) * matrix
                irect_obj = pix.irect
                if not hasattr(irect_obj, "x0"):
                    irect_obj = fitz.IRect(irect_obj)

                total_trim_x = max(0, width_px - target_width)
                total_trim_y = max(0, height_px - target_height)

                left_extra = max(0.0, float(device_rect.x0) - float(irect_obj.x0))
                right_extra = max(0.0, float(irect_obj.x1) - float(device_rect.x1))
                top_extra = max(0.0, float(device_rect.y0) - float(irect_obj.y0))
                bottom_extra = max(0.0, float(irect_obj.y1) - float(device_rect.y1))

                left_trim = min(int(round(left_extra)), total_trim_x)
                right_trim = max(0, total_trim_x - left_trim)
                top_trim = min(int(round(top_extra)), total_trim_y)
                bottom_trim = max(0, total_trim_y - top_trim)

                start_col = min(max(left_trim, 0), max(0, width_px - target_width))
                end_col = width_px - min(max(right_trim, 0), max(0, width_px - target_width - start_col))
                start_row = min(max(top_trim, 0), max(0, height_px - target_height))
                end_row = height_px - min(max(bottom_trim, 0), max(0, height_px - target_height - start_row))

                clip_width = max(1, end_col - start_col)
                clip_height = max(1, end_row - start_row)
                row_bytes = clip_width * total_components
                cropped = bytearray(clip_height * row_bytes)
                samples = pix.samples
                for row in range(clip_height):
                    src_row = start_row + row
                    src_start = src_row * stride + start_col * total_components
                    src_end = src_start + row_bytes
                    if src_end > len(samples):  # pragma: no cover - defensive guard
                        raise RegionCaptureError("Datos incompletos al recortar la captura")
                    cropped[row * row_bytes : (row + 1) * row_bytes] = samples[src_start:src_end]

                pix = fitz.Pixmap(pix.colorspace, clip_width, clip_height, bytes(cropped), pix.alpha)
                width_px = int(pix.width)
                height_px = int(pix.height)
            if width_px <= 0 or height_px <= 0:
                raise RegionCaptureError("La captura devolvió una imagen sin dimensiones válidas")

            # If PyMuPDF performed internal rounding, trust the pixmap dimensions
            # but keep metadata of the theoretical target size for traceability.
            metadata = {
                "page_index": int(spec.page_index),
                "rect_pt": list(rect),
                "dpi": int(round(dpi_value)),
                "dpi_exact": dpi_value,
                "label": spec.label,
                "width_px": width_px,
                "height_px": height_px,
                "target_width_px": expected_w,
                "target_height_px": expected_h,
                "transparent": transparent,
            }

            image_name = f"{prefix}-p{int(spec.page_index):04d}-r{idx:03d}.png"
            image_path = output_dir / image_name
            pix.save(str(image_path))

            metadata_path = image_path.with_suffix(".json")
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

            xobject = _pixmap_to_xobject(pix, transparent=transparent)

            captures.append(
                CapturedRegionImage(
                    image_path=image_path,
                    metadata_path=metadata_path,
                    page_index=int(spec.page_index),
                    rect_pt=rect,
                    dpi=int(round(dpi_value)),
                    label=spec.label,
                    width_px=width_px,
                    height_px=height_px,
                    transparent=transparent,
                    xobject=xobject,
                )
            )

    return tuple(captures)


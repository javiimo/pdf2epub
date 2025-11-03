"""PDF preprocessing pipeline to rasterize formulas and tables into images."""
from __future__ import annotations

import concurrent.futures
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional, Sequence, Tuple

from core.runner.coordinates import pixels_to_pdf_rect
from core.runner.detect_prep import DetectionPrepError, PageImage, prepare_page_images
from core.runner.layout import LayoutBox, LayoutError, infer_layout_on_image
from core.runner.pdf_insertion import (
    ImageInsertOptions,
    ImageInsertSpec,
    insert_pdf_images,
)
from core.runner.postprocess import PostprocessOptions, postprocess_math_and_tables
from core.runner.redaction import RedactionRegion, redact_and_save_pdf
from core.runner.region_capture import (
    CaptureOptions,
    RegionCaptureError,
    RegionSpec,
    capture_pdf_regions,
)
from core.runner.tables import TableDetectError, fuse_tables_with_layout, infer_tables_on_image

SendFn = Callable[[str, str], None]


class PreprocessError(RuntimeError):
    """Raised when the preprocessing pipeline cannot complete."""


_PREPROC_DEFAULTS: Mapping[str, object] = {
    "preproc.enabled": False,
    "preproc.device": "cpu",
    "preproc.convert_math": True,
    "preproc.convert_inline": True,
    "preproc.convert_tables": True,
    "preproc.min_area_px": 150,
    "preproc.margin_pts": 1.0,
    "preproc.pages": "",
    "preproc.paddleocr_path": None,
    "preproc.dpi": 360,
}


@dataclass(frozen=True)
class PreprocessOptions:
    """Options controlling the preprocessing pipeline."""

    enabled: bool = False
    device: str = "cpu"
    convert_math: bool = True
    convert_inline: bool = True
    convert_tables: bool = True
    min_area_px: int = 150
    margin_pts: float = 1.0
    page_scope: Tuple[int, ...] = ()
    dpi: int = 360
    paddleocr_path: Optional[str] = None

    @property
    def has_targets(self) -> bool:
        return bool(self.convert_math or self.convert_tables)

    def should_process(self) -> bool:
        return bool(self.enabled and self.has_targets)

    def wants_page(self, page_index: int) -> bool:
        if not self.page_scope:
            return True
        return int(page_index) in self.page_scope


@dataclass(frozen=True)
class PreprocessResult:
    """Summary of the preprocessing work performed on a PDF."""

    output_pdf: Path
    total_pages: int
    processed_pages: Tuple[int, ...]
    replaced_regions: int
    device: str
    workers: int


def _parse_page_scope(raw: object) -> Tuple[int, ...]:
    if not raw:
        return tuple()
    if isinstance(raw, (list, tuple)):
        items: Iterable[object] = raw
    else:
        text = str(raw).strip()
        if not text:
            return tuple()
        items = text.split(",")
    pages: set[int] = set()
    for item in items:
        token = str(item).strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            try:
                start = int(start_text)
                end = int(end_text)
            except ValueError as exc:
                raise PreprocessError(f"Rango de páginas inválido: '{token}'") from exc
            if start > end:
                start, end = end, start
            for value in range(start, end + 1):
                if value > 0:
                    pages.add(value)
        else:
            try:
                page = int(token)
            except ValueError as exc:
                raise PreprocessError(f"Página inválida: '{token}'") from exc
            if page > 0:
                pages.add(page)
    return tuple(sorted(pages))


def options_from_extras(extras: Mapping[str, object]) -> PreprocessOptions:
    payload: dict[str, object] = dict(_PREPROC_DEFAULTS)
    for key, value in extras.items():
        if key.startswith("preproc."):
            payload[key] = value
    enabled = bool(payload.get("preproc.enabled", False))
    device = str(payload.get("preproc.device", "cpu") or "cpu").lower()
    convert_math = bool(payload.get("preproc.convert_math", True))
    convert_inline = bool(payload.get("preproc.convert_inline", True))
    convert_tables = bool(payload.get("preproc.convert_tables", True))
    try:
        min_area = int(payload.get("preproc.min_area_px", 150) or 0)
    except (TypeError, ValueError):
        raise PreprocessError("'preproc.min_area_px' debe ser un entero")
    try:
        margin = float(payload.get("preproc.margin_pts", 1.0) or 0.0)
    except (TypeError, ValueError):
        raise PreprocessError("'preproc.margin_pts' debe ser un número")
    page_scope = _parse_page_scope(payload.get("preproc.pages", ""))
    paddleocr_path_raw = payload.get("preproc.paddleocr_path")
    paddle = str(paddleocr_path_raw) if paddleocr_path_raw not in (None, "") else None
    try:
        dpi = int(payload.get("preproc.dpi", 360) or 360)
    except (TypeError, ValueError):
        raise PreprocessError("'preproc.dpi' debe ser un entero")
    return PreprocessOptions(
        enabled=enabled,
        device=device,
        convert_math=convert_math,
        convert_inline=convert_inline,
        convert_tables=convert_tables,
        min_area_px=max(0, min_area),
        margin_pts=max(0.0, margin),
        page_scope=page_scope,
        paddleocr_path=paddle,
        dpi=max(200, min(600, dpi)),
    )


def _query_gpu_memory_mib() -> Optional[int]:
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.total",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    line = (completed.stdout or "").strip().splitlines()
    if not line:
        return None
    first = line[0].strip()
    if not first:
        return None
    try:
        return int(first)
    except ValueError:
        return None


def _estimate_worker_count(device: str, pages: Sequence[PageImage]) -> int:
    if not pages:
        return 1
    if not device.startswith("gpu"):
        return 1
    total_mem = _query_gpu_memory_mib()
    if total_mem is None:
        return 1
    # Estimate memory per page using width*height*4 bytes plus headroom.
    per_page = 0
    for page in pages:
        pixels = max(1, int(page.width_px)) * max(1, int(page.height_px))
        estimate = int(pixels * 4 * 2)
        per_page = max(per_page, estimate)
    if per_page <= 0:
        return 1
    available_bytes = int(total_mem * 1024 * 1024 * 0.7)
    workers = max(1, available_bytes // per_page)
    return max(1, min(len(pages), workers))


def _process_single_page(
    page: PageImage,
    *,
    options: PreprocessOptions,
) -> Tuple[PageImage, Tuple[LayoutBox, ...]]:
    paddle = options.paddleocr_path or "paddleocr"
    layout = infer_layout_on_image(
        page.image_path,
        device=options.device,
        paddleocr_path=paddle,
    )
    tables = infer_tables_on_image(
        page.image_path,
        device=options.device,
        paddleocr_path=paddle,
    )
    fused = fuse_tables_with_layout(layout, tables, iou_threshold=0.3)
    post = postprocess_math_and_tables(
        fused,
        options=PostprocessOptions(
            dpi=page.dpi,
            min_area_px=options.min_area_px,
            margin_pts=options.margin_pts,
            suppress_inline_math=not options.convert_inline,
        ),
    )
    if not options.convert_math or not options.convert_tables:
        filtered: list[LayoutBox] = []
        for box in post.boxes:
            if box.label == "mathblock" and not options.convert_math:
                continue
            if box.label == "tableblock" and not options.convert_tables:
                continue
            filtered.append(box)
        return page, tuple(filtered)
    return page, post.boxes


def preprocess_pdf(
    pdf_path: Path,
    *,
    workspace: Path,
    options: PreprocessOptions,
    send: Optional[SendFn] = None,
) -> PreprocessResult:
    if not options.should_process():
        raise PreprocessError("El preprocesado no está habilitado o no tiene objetivos activos")

    source_pdf = Path(pdf_path)
    if not source_pdf.exists():
        raise PreprocessError(f"No se encontró el PDF de origen: {source_pdf}")

    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    detectors_dir = workspace / "detectors"
    captures_dir = workspace / "captures"
    redacted_pdf = workspace / "redacted.pdf"
    final_pdf = workspace / "preprocessed.pdf"

    def _log(message: str) -> None:
        if send:
            send("message", message)

    _log("Rasterizando páginas para detección de fórmulas/tablas…")
    try:
        pages = list(prepare_page_images(source_pdf, detectors_dir, dpi=options.dpi))
    except (DetectionPrepError, RegionCaptureError, RuntimeError) as exc:
        raise PreprocessError(str(exc)) from exc

    selected_pages = [p for p in pages if options.wants_page(p.page_index)]
    if not selected_pages:
        _log("Sin páginas seleccionadas para preprocesar; se usará el PDF original.")
        return PreprocessResult(
            output_pdf=source_pdf,
            total_pages=len(pages),
            processed_pages=tuple(),
            replaced_regions=0,
            device=options.device,
            workers=1,
        )

    workers = _estimate_worker_count(options.device, selected_pages)
    _log(
        f"Ejecutando detectores en {len(selected_pages)} páginas "
        f"({options.device}, lote máximo {workers})."
    )

    boxes_by_page: dict[int, Tuple[LayoutBox, ...]] = {}
    detection_errors: list[str] = []

    def _handle_page(page: PageImage) -> None:
        try:
            page_result = _process_single_page(page, options=options)
        except LayoutError as exc:
            detection_errors.append(f"Layout falló en p.{page.page_index}: {exc}")
            return
        except TableDetectError as exc:
            detection_errors.append(f"TSR falló en p.{page.page_index}: {exc}")
            return
        page_obj, layout_boxes = page_result
        boxes_by_page[page_obj.page_index] = layout_boxes

    if workers > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            list(executor.map(_handle_page, selected_pages))
    else:
        for page in selected_pages:
            _handle_page(page)

    if detection_errors and len(detection_errors) == len(selected_pages):
        raise PreprocessError("Los detectores fallaron en todas las páginas seleccionadas")

    redaction_regions: list[RedactionRegion] = []
    insert_specs: list[ImageInsertSpec] = []
    total_regions = 0

    for page in selected_pages:
        boxes = boxes_by_page.get(page.page_index)
        if not boxes:
            continue
        specs_for_capture: list[RegionSpec] = []
        for box in boxes:
            rect = pixels_to_pdf_rect(page, (box.x, box.y, box.width, box.height))
            redaction_regions.append(
                RedactionRegion(page_index=page.page_index, rect_pt=rect, label=box.label)
            )
            specs_for_capture.append(
                RegionSpec(page_index=page.page_index, rect_pt=rect, label=box.label)
            )

        if not specs_for_capture:
            continue
        page_capture_dir = captures_dir / f"page-{page.page_index:03d}"
        page_capture_dir.mkdir(parents=True, exist_ok=True)
        effective_dpi = max(300.0, min(600.0, page.scale * 72.0))
        try:
            captures = capture_pdf_regions(
                source_pdf,
                specs_for_capture,
                output_dir=page_capture_dir,
                options=CaptureOptions(dpi=effective_dpi, image_prefix=f"page{page.page_index:03d}"),
            )
        except RegionCaptureError as exc:
            raise PreprocessError(f"Falló la captura de regiones en p.{page.page_index}: {exc}") from exc
        for capture in captures:
            insert_specs.append(ImageInsertSpec(capture=capture))
        total_regions += len(captures)

    if not insert_specs:
        _log("No se detectaron regiones para convertir en imágenes.")
        return PreprocessResult(
            output_pdf=source_pdf,
            total_pages=len(pages),
            processed_pages=tuple(),
            replaced_regions=0,
            device=options.device,
            workers=workers,
        )

    _log("Aplicando redacción de contenido original…")
    try:
        redact_and_save_pdf(source_pdf, redacted_pdf, redaction_regions)
    except Exception as exc:  # pragma: no cover - delega en PyMuPDF
        raise PreprocessError(f"No se pudo redactar el PDF: {exc}") from exc
    _log("Insertando imágenes rasterizadas en el PDF…")
    try:
        insert_pdf_images(
            redacted_pdf,
            insert_specs,
            output_pdf=final_pdf,
            options=ImageInsertOptions(paint_background=False, overlay=True),
        )
    except Exception as exc:  # pragma: no cover - delega en PyMuPDF
        raise PreprocessError(f"No se pudieron insertar las imágenes: {exc}") from exc

    processed_pages = tuple(sorted({spec.capture.page_index for spec in insert_specs}))
    if not detection_errors:
        _log("Preprocesado completado sin errores.")
    else:
        for error in detection_errors:
            _log(f"[WARN] {error}")

    return PreprocessResult(
        output_pdf=final_pdf,
        total_pages=len(pages),
        processed_pages=processed_pages,
        replaced_regions=total_regions,
        device=options.device,
        workers=workers,
    )

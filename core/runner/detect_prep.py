"""Preparatory helpers for math/table detection on PDF pages.

This module implements the checklist items under the *Preparación de detección*
section. It normalizes page rasterization, orchestrates PaddleOCR layout and
table detectors, fuses/filters the resulting boxes, and flags detections that
already correspond to raster images inside the PDF.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:  # PyMuPDF is optional at import time; required when executing helpers.
    import fitz  # type: ignore
except Exception:  # pragma: no cover - optional dependency guard
    fitz = None  # type: ignore[assignment]

from .layout import LayoutBox, LayoutResult, LayoutError, infer_layout_on_image
from .rasterize import RasterizeError, rasterize_pdf_to_png
from .tables import TableBox, TableDetectError, fuse_tables_with_layout, infer_tables_on_image

__all__ = [
    "DetectionPrepError",
    "PageImage",
    "DetectedRegion",
    "DetectionPrepOptions",
    "DetectionPreparation",
    "prepare_detection",
    "prepare_page_images",
    "run_detectors_on_pages",
    "mark_preexisting_raster_regions",
]


class DetectionPrepError(RuntimeError):
    """Raised when preparing detection artifacts fails."""


@dataclass(frozen=True)
class PageImage:
    """Metadata for a rasterized PDF page used by detectors."""

    page_index: int
    dpi: int
    image_path: Path
    width_px: int
    height_px: int
    cropbox: Tuple[float, float, float, float]
    mediabox: Tuple[float, float, float, float]
    rotate: int

    @property
    def width_pts(self) -> float:
        return float(self.cropbox[2] - self.cropbox[0])

    @property
    def height_pts(self) -> float:
        return float(self.cropbox[3] - self.cropbox[1])

    @property
    def scale_x(self) -> float:
        pts = self.width_pts
        return float(self.width_px) / pts if pts else 0.0

    @property
    def scale_y(self) -> float:
        pts = self.height_pts
        return float(self.height_px) / pts if pts else 0.0


@dataclass(frozen=True)
class DetectedRegion:
    """Normalized detection box returned by PaddleOCR detectors."""

    page_index: int
    label: str  # "math" or "table"
    score: float
    x: int
    y: int
    width: int
    height: int
    source: str  # layout | table
    skip_due_to_image: bool = False

    @property
    def rect(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.width, self.height)


@dataclass(frozen=True)
class DetectionPrepOptions:
    """Options controlling how detection preparation runs."""

    dpi: int = 360
    device: str = "cpu"
    paddleocr_path: Optional[str] = None
    layout_threshold: float = 0.3
    table_iou_threshold: float = 0.3
    min_area_px: int = 150
    min_side_px: int = 8
    skip_image_iou: float = 0.6


@dataclass(frozen=True)
class DetectionPreparation:
    """Aggregate output of detection preparation."""

    pages: Tuple[PageImage, ...]
    regions: Tuple[DetectedRegion, ...]


def _read_png_dimensions(path: Path) -> Tuple[int, int]:
    with Path(path).open("rb") as fh:
        data = fh.read(24)
    if len(data) < 24 or data[12:16] != b"IHDR":
        raise DetectionPrepError(f"No se pudieron leer las dimensiones PNG de {path}")
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return int(width), int(height)


def _detect_paddle_cli() -> str:
    try:
        root = Path(__file__).resolve().parents[2]
        candidate = root / ".venv" / "bin" / "paddleocr"
        if candidate.exists():
            return str(candidate)
    except Exception:
        pass
    return "paddleocr"


def _list_pdftocairo_outputs(prefix: Path) -> List[Tuple[int, Path]]:
    prefix = Path(prefix)
    parent = prefix.parent
    stem = prefix.name
    items: List[Tuple[int, Path]] = []
    for png in sorted(parent.glob(f"{stem}-*.png")):
        name = png.name.rsplit(".", 1)[0]
        try:
            page = int(name.split("-")[-1])
        except Exception:
            continue
        items.append((page, png))
    items.sort(key=lambda t: t[0])
    return items


def _require_fitz() -> None:
    if fitz is None:  # pragma: no cover - environment dependent
        raise DetectionPrepError("PyMuPDF (fitz) no disponible para leer metadatos del PDF")


def _tuple_from_rect(rect: Any) -> Tuple[float, float, float, float]:
    return (float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1))


def _load_page_boxes(pdf_path: Path) -> List[Tuple[Tuple[float, float, float, float], Tuple[float, float, float, float], int]]:
    _require_fitz()
    doc = fitz.open(pdf_path)  # type: ignore[arg-type]
    try:
        items: List[Tuple[Tuple[float, float, float, float], Tuple[float, float, float, float], int]] = []
        for page in doc:
            crop = _tuple_from_rect(page.cropbox)
            media = _tuple_from_rect(page.mediabox)
            rotate = int(page.rotation or 0)
            items.append((crop, media, rotate))
        return items
    finally:
        doc.close()


def prepare_page_images(
    pdf_path: Path,
    out_dir: Path,
    *,
    dpi: int,
    rasterize: Callable[..., Path] = rasterize_pdf_to_png,
    run=None,
) -> Tuple[PageImage, ...]:
    """Rasterize PDF pages and collect metadata for detection."""

    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    prefix = out_dir / "page"
    try:
        rasterize(pdf_path, prefix, dpi=dpi, run=run)
    except RasterizeError as exc:
        raise DetectionPrepError(str(exc)) from exc

    pngs = _list_pdftocairo_outputs(prefix)
    if not pngs:
        raise DetectionPrepError("pdftocairo no generó PNGs para los detectores")

    page_boxes = _load_page_boxes(pdf_path)
    pages: List[PageImage] = []
    for page_idx, png_path in pngs:
        if page_idx - 1 >= len(page_boxes):
            raise DetectionPrepError(f"Metadatos de página no encontrados para índice {page_idx}")
        cropbox, mediabox, rotate = page_boxes[page_idx - 1]
        width_px, height_px = _read_png_dimensions(png_path)
        pages.append(
            PageImage(
                page_index=int(page_idx),
                dpi=int(dpi),
                image_path=png_path,
                width_px=int(width_px),
                height_px=int(height_px),
                cropbox=cropbox,
                mediabox=mediabox,
                rotate=int(rotate),
            )
        )
    return tuple(pages)


def _normalize_label(label: str) -> Optional[str]:
    lbl = label.lower().strip()
    if lbl in {"formula", "equation", "math", "mathblock"}:
        return "math"
    if lbl in {"table", "tableblock"}:
        return "table"
    return None


def _filter_boxes(
    boxes: Iterable[LayoutBox],
    *,
    min_area: int,
    min_side: int,
) -> List[LayoutBox]:
    filtered: List[LayoutBox] = []
    for box in boxes:
        label = _normalize_label(box.label)
        if label is None:
            continue
        area = int(box.width) * int(box.height)
        if area < min_area:
            continue
        if box.width < min_side or box.height < min_side:
            continue
        filtered.append(LayoutBox(label=label, score=box.score, x=box.x, y=box.y, width=box.width, height=box.height))
    return filtered


def run_detectors_on_pages(
    pages: Sequence[PageImage],
    *,
    options: DetectionPrepOptions,
    layout_runner: Callable[..., LayoutResult] | None = None,
    table_runner: Callable[..., Tuple[TableBox, ...]] | None = None,
    run=None,
) -> Tuple[DetectedRegion, ...]:
    """Execute layout + TSR detectors and return normalized regions."""

    paddle = options.paddleocr_path or _detect_paddle_cli()
    layout_runner = layout_runner or (lambda path: infer_layout_on_image(
        path,
        threshold=options.layout_threshold,
        device=options.device,
        paddleocr_path=paddle,
        run=run,
    ))
    table_runner = table_runner or (lambda path: infer_tables_on_image(
        path,
        device=options.device,
        paddleocr_path=paddle,
        run=run,
    ))

    regions: List[DetectedRegion] = []
    for page in pages:
        try:
            layout = layout_runner(page.image_path)
        except LayoutError as exc:
            raise DetectionPrepError(f"Layout fallo en p.{page.page_index}: {exc}") from exc
        try:
            tables = table_runner(page.image_path)
        except TableDetectError as exc:
            raise DetectionPrepError(f"TSR fallo en p.{page.page_index}: {exc}") from exc

        fused = fuse_tables_with_layout(layout, tables, iou_threshold=options.table_iou_threshold)
        filtered = _filter_boxes(fused.boxes, min_area=options.min_area_px, min_side=options.min_side_px)
        for box in filtered:
            label = _normalize_label(box.label)
            if label is None:
                continue
            regions.append(
                DetectedRegion(
                    page_index=page.page_index,
                    label=label,
                    score=float(box.score),
                    x=int(box.x),
                    y=int(box.y),
                    width=int(box.width),
                    height=int(box.height),
                    source="table" if label == "table" else "layout",
                )
            )
    return tuple(regions)


def _iou(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return 0.0
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    union = (aw * ah) + (bw * bh) - inter
    if union <= 0:
        return 0.0
    return inter / union


def _extract_existing_image_boxes(pdf_path: Path) -> Dict[int, Tuple[Tuple[float, float, float, float], ...]]:
    _require_fitz()
    doc = fitz.open(pdf_path)  # type: ignore[arg-type]
    try:
        result: Dict[int, Tuple[Tuple[float, float, float, float], ...]] = {}
        for index, page in enumerate(doc, start=1):
            rects: List[Tuple[float, float, float, float]] = []
            blocks = page.get_text("dict").get("blocks", [])
            for block in blocks:
                if block.get("type") == 1 and block.get("bbox"):
                    x0, y0, x1, y1 = block["bbox"]
                    rects.append((float(x0), float(y0), float(x1), float(y1)))
            result[index] = tuple(rects)
        return result
    finally:
        doc.close()


def _convert_pdf_rect_to_pixels(rect: Tuple[float, float, float, float], page: PageImage) -> Tuple[float, float, float, float]:
    x0, y0, x1, y1 = rect
    crop_x0, crop_y0, _, _ = page.cropbox
    scale_x = page.scale_x or 0.0
    scale_y = page.scale_y or 0.0
    width = (x1 - x0) * scale_x
    height = (y1 - y0) * scale_y
    x = (x0 - crop_x0) * scale_x
    # PDF coordinates origin at bottom-left; convert to image top-left space
    y_top = page.height_px - ((y1 - crop_y0) * scale_y)
    return (x, y_top, width, height)


def mark_preexisting_raster_regions(
    pdf_path: Path,
    pages: Sequence[PageImage],
    regions: Sequence[DetectedRegion],
    *,
    iou_threshold: float,
    image_box_loader: Callable[[Path], Dict[int, Tuple[Tuple[float, float, float, float], ...]]] | None = None,
) -> Tuple[DetectedRegion, ...]:
    """Mark detections that overlap existing raster images in the PDF."""

    loader = image_box_loader or _extract_existing_image_boxes
    image_boxes = loader(Path(pdf_path))
    pages_by_index: Dict[int, PageImage] = {p.page_index: p for p in pages}

    updated: List[DetectedRegion] = []
    for region in regions:
        page = pages_by_index.get(region.page_index)
        if page is None:
            updated.append(region)
            continue
        pdf_rects = image_boxes.get(region.page_index, ())
        skip = False
        if pdf_rects:
            region_rect = (float(region.x), float(region.y), float(region.width), float(region.height))
            for rect in pdf_rects:
                rect_px = _convert_pdf_rect_to_pixels(rect, page)
                if _iou(region_rect, rect_px) >= iou_threshold:
                    skip = True
                    break
        updated.append(replace(region, skip_due_to_image=skip))
    return tuple(updated)


def prepare_detection(
    pdf_path: Path,
    out_dir: Path,
    *,
    options: Optional[DetectionPrepOptions] = None,
    run=None,
) -> DetectionPreparation:
    """Run the full detection preparation pipeline for a PDF."""

    opts = options or DetectionPrepOptions()
    pages = prepare_page_images(pdf_path, out_dir, dpi=opts.dpi, run=run)
    regions = run_detectors_on_pages(pages, options=opts, run=run)
    marked = mark_preexisting_raster_regions(
        pdf_path,
        pages,
        regions,
        iou_threshold=opts.skip_image_iou,
    )
    return DetectionPreparation(pages=pages, regions=marked)


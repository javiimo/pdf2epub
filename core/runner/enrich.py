"""Enrich an OEB preview with math/table images detected by ML.

This module stitches together the selective rasterization pipeline:
  - Render subset PDF pages to PNG for detectors (pdftocairo).
  - Run layout and table detectors (PaddleOCR CLIs), fuse and postprocess.
  - Rasterize only detected boxes from the subset PDF to individual PNGs.
  - Copy images into the OEB, update OPF manifest, insert <figure><img ...>.
  - Ensure a minimal CSS is present and linked in spine HTML.

It is designed to be called after a successful run_preview(), receiving the
PreviewResult with the temporary workspace and subset PDF reference.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from core.oeb.assemble import (
    FigureSpec,
    add_images_to_manifest,
    copy_images_into_oeb,
    insert_figures_into_html,
    install_default_css,
)
from core.runner.layout import LayoutError, LayoutResult, infer_layout_on_image
from core.runner.postprocess import PostprocessOptions, postprocess_math_and_tables
from core.runner.preview import PreviewResult
from core.runner.rasterize import rasterize_boxes_from_pdf, rasterize_pdf_to_png
from core.runner.tables import TableDetectError, fuse_tables_with_layout, infer_tables_on_image

__all__ = ["EnrichOptions", "EnrichResult", "enrich_oeb_with_ml"]


SendFn = Callable[[str, tuple[str, str] | str], None]


@dataclass(frozen=True)
class EnrichOptions:
    """Options steering the enrichment process."""

    dpi: int = 360
    device: str = "cpu"
    paddleocr_path: Optional[str] = None  # auto-detect .venv/bin/paddleocr when None
    suppress_math_inside_tables: bool = True
    table_cover_threshold: float = 0.9


@dataclass(frozen=True)
class EnrichResult:
    """Summary of the enrichment outcome."""

    total_pages: int
    total_boxes: int
    images_written: Tuple[Path, ...]


def _detect_paddle_cli() -> str:
    # Prefer repository-local .venv bin when present, else fallback to PATH
    try:
        root = Path(__file__).resolve().parents[2]
        candidate = root / ".venv" / "bin" / "paddleocr"
        if candidate.exists():
            return str(candidate)
    except Exception:
        pass
    return "paddleocr"


def _list_pdftocairo_outputs(prefix: Path) -> List[Tuple[int, Path]]:
    """Return list of (page_index, png_path) for a pdftocairo output prefix.

    Accepts both non-padded and zero-padded page numbers.
    """
    prefix = Path(prefix)
    parent = prefix.parent
    stem = prefix.name
    items: List[Tuple[int, Path]] = []
    for p in sorted(parent.glob(f"{stem}-*.png")):
        name = p.name  # e.g., page-1.png or page-001.png
        try:
            base = name.rsplit(".", 1)[0]
            num_text = base.split("-")[-1]
            page = int(num_text)
        except Exception:
            continue
        items.append((page, p))
    items.sort(key=lambda t: t[0])
    return items


def enrich_oeb_with_ml(
    preview: PreviewResult,
    *,
    options: Optional[EnrichOptions] = None,
    send: Optional[SendFn] = None,
    run=None,
) -> EnrichResult:
    """Detect math/tables and inject images into the OEB from a preview.

    Args:
        preview: Output from run_preview().
        options: Enrichment options (dpi, device, thresholds).
        send: Optional UI callback for logging. Called as send("message", str)
              or send("stream", ("stdout"|"stderr", text)) per notebook UI.
        run: Optional subprocess runner compatible with subprocess.run; passed
             into the underlying helpers for streaming support.

    Returns:
        EnrichResult with counts and image locations.
    """
    opts = options or EnrichOptions()
    dpi = int(opts.dpi)
    device = str(opts.device or "cpu")
    paddle = opts.paddleocr_path or _detect_paddle_cli()

    # 1) Rasterize subset PDF pages to PNG for detectors
    if send:
        send("message", f"Rasterizando páginas para detectores a {dpi} dpi…")
    detectors_dir = Path(preview.workspace.path) / "detectors"
    prefix = detectors_dir / "page"
    rasterize_pdf_to_png(
        preview.subset_pdf,
        prefix,
        dpi=dpi,
        run=run,
    )
    page_images = _list_pdftocairo_outputs(prefix)
    if send:
        send("message", f"Páginas rasterizadas: {len(page_images)}")

    # 2) Run detectors page by page, fuse and postprocess
    all_boxes: List[Tuple[int, LayoutResult]] = []
    total_boxes = 0
    for page_idx, img_path in page_images:
        if send:
            send("message", f"Detectando layout/tabla en página {page_idx}…")
        try:
            layout = infer_layout_on_image(img_path, device=device, paddleocr_path=paddle, run=run)
        except LayoutError as exc:
            if send:
                send("message", f"[WARN] Layout fallo en p.{page_idx}: {exc}")
            continue
        try:
            tables = infer_tables_on_image(img_path, device=device, paddleocr_path=paddle, run=run)
        except TableDetectError as exc:
            if send:
                send("message", f"[WARN] TSR fallo en p.{page_idx}: {exc}")
            tables = ()
        fused = fuse_tables_with_layout(layout, tables, iou_threshold=0.3)
        post = postprocess_math_and_tables(
            fused,
            options=PostprocessOptions(
                dpi=dpi,
                suppress_math_inside_tables=bool(opts.suppress_math_inside_tables),
                table_cover_threshold=float(opts.table_cover_threshold),
            ),
        )
        if post.boxes:
            all_boxes.append((page_idx, post))
            total_boxes += len(post.boxes)

    if send:
        send("message", f"Cajas finales detectadas: {total_boxes}")
    if total_boxes == 0:
        # Ensure CSS is still present for consistency
        try:
            install_default_css(preview.oeb_output)
        except Exception:
            pass
        return EnrichResult(total_pages=len(page_images), total_boxes=0, images_written=tuple())

    # 3) Rasterize selective boxes from subset PDF
    if send:
        send("message", "Rasterizando regiones detectadas…")
    out_dir = Path(preview.workspace.path) / "selective"
    part_images: List[Path] = []
    labels: List[str] = []
    for page_idx, post in all_boxes:
        imgs = rasterize_boxes_from_pdf(
            preview.subset_pdf,
            out_dir,
            int(page_idx),
            list(post.boxes),
            dpi=dpi,
            run=run,
        )
        part_images.extend(imgs)
        labels.extend([b.label for b in post.boxes])

    # 4) Copy into OEB and update OPF manifest
    copied = copy_images_into_oeb(preview.oeb_output, list(part_images))
    add_images_to_manifest(preview.oeb_output / "content.opf", list(copied))

    # 5) Insert figures in the first spine HTML and ensure CSS
    try:
        figures = [FigureSpec(image_path=img, label=lbl) for img, lbl in zip(copied, labels)]
        insert_figures_into_html(preview.spine_first_html, figures)
    finally:
        # CSS is a best-effort enhancement
        try:
            install_default_css(preview.oeb_output)
        except Exception:
            pass

    return EnrichResult(total_pages=len(page_images), total_boxes=total_boxes, images_written=tuple(copied))


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
    insert_figures_inline,
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
    # Whether to remove short math-like paragraphs adjacent to figures in HTML
    remove_math_text: bool = True
    # Output image format for selective rasterization (png or jpeg)
    image_format: str = "png"
    # JPEG quality (when image_format is jpeg)
    jpeg_quality: int = 85


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
    placements: List[tuple[int, int, int]] = []  # (page_idx, y, page_height)

    def _read_png_dims(png_path: Path) -> tuple[int, int]:
        # Minimal PNG header reader: IHDR at byte 16..24 contains width/height (big-endian)
        try:
            with open(png_path, 'rb') as f:
                header = f.read(24)
            if len(header) >= 24 and header[12:16] == b'IHDR':
                width = int.from_bytes(header[16:20], 'big')
                height = int.from_bytes(header[20:24], 'big')
                return int(width), int(height)
        except Exception:
            return (0, 0)
    coverage_by_page: dict[int, float] = {}
    for page_idx, post in all_boxes:
        imgs = rasterize_boxes_from_pdf(
            preview.subset_pdf,
            out_dir,
            int(page_idx),
            list(post.boxes),
            dpi=dpi,
            image_format=str((opts.image_format or "png").lower()),
            jpeg_quality=int(opts.jpeg_quality or 85),
            run=run,
        )
        part_images.extend(imgs)
        labels.extend([b.label for b in post.boxes])
        # Collect placement hints matching the order of boxes/images
        pw, ph = _read_png_dims(post.image_path) if hasattr(post, 'image_path') else (0, 0)
        if pw > 0 and ph > 0:
            try:
                total_area = float(pw * ph)
                area_boxes = sum(max(0, b.width) * max(0, b.height) for b in post.boxes)
                coverage_by_page[int(page_idx)] = min(1.0, float(area_boxes) / total_area)
            except Exception:
                pass
        page_h = ph
        for b in post.boxes:
            placements.append((int(page_idx), int(b.y), int(page_h)))

    # 4) Copy into OEB and update OPF manifest
    copied = copy_images_into_oeb(preview.oeb_output, list(part_images))
    add_images_to_manifest(preview.oeb_output / "content.opf", list(copied))

    # 5) Insert figures inline across spine HTMLs using page-break heuristics
    try:
        figures = []
        for (img, lbl), (pidx, y, ph) in zip(zip(copied, labels), placements):
            figures.append(FigureSpec(image_path=img, label=lbl, page_index=pidx, y=y, page_height=ph))

        # Distribute figures across linear spine files according to page segments
        spine_files = [it.href for it in (preview.spine_linear_items or [])]
        placed_ratio = 0
        placed_fallback = 0
        if not spine_files:
            # Fallback to the first HTML only
            rep = insert_figures_inline(preview.spine_first_html, figures, page_offset=1, remove_math_text=bool(opts.remove_math_text))
            try:
                placed_ratio += int(getattr(rep, "placed_ratio", 0))
                placed_fallback += int(getattr(rep, "placed_fallback", 0))
            except Exception:
                pass
        else:
            # Count segments per file using similar heuristics as assemble._find_pagebreaks
            import re

            def _count_segments(path: Path) -> int:
                try:
                    t = path.read_text(encoding='utf-8', errors='ignore')
                except Exception:
                    return 1
                patterns = [
                    r"<a[^>]+(?:id|name)=(?:\"|')calibre_pb_\d+(?:\"|')[^>]*>\s*</a>",
                    r"<[^>]+epub:type=(?:\"|')pagebreak(?:\"|')[^>]*>",
                    r"<hr[^>]*class=(?:\"|')[^\"']*pagebreak[^\"']*(?:\"|')[^>]*>",
                    r"<span[^>]*class=(?:\"|')[^\"']*pagebreak[^\"']*(?:\"|')[^>]*>\s*</span>",
                ]
                breaks = 0
                for pat in patterns:
                    breaks += len(list(re.finditer(pat, t, re.IGNORECASE | re.DOTALL)))
                return max(1, breaks + 1)

            seg_counts = [ _count_segments(p) for p in spine_files ]
            # Assign page ranges sequentially
            start = 1
            file_ranges: List[tuple[Path, int, int]] = []  # (file, start_page, end_page)
            for f, nseg in zip(spine_files, seg_counts):
                end = start + nseg - 1
                file_ranges.append((f, start, end))
                start = end + 1

            # Group figures into files
            by_file: dict[Path, List[FigureSpec]] = {f: [] for f, _, _ in file_ranges}
            for fig in figures:
                assigned = False
                for f, s, e in file_ranges:
                    if fig.page_index is not None and s <= fig.page_index <= e:
                        by_file[f].append(fig)
                        assigned = True
                        break
                if not assigned:
                    # Put unassigned into the first file
                    by_file[file_ranges[0][0]].append(fig)

            # Insert into each file with the appropriate offset
            for f, s, e in file_ranges:
                items = by_file.get(f) or []
                if not items:
                    continue
                rep = insert_figures_inline(f, items, page_offset=s, remove_math_text=bool(opts.remove_math_text))
                try:
                    placed_ratio += int(getattr(rep, "placed_ratio", 0))
                    placed_fallback += int(getattr(rep, "placed_fallback", 0))
                except Exception:
                    pass
    finally:
        # CSS is a best-effort enhancement
        try:
            install_default_css(preview.oeb_output)
        except Exception:
            pass

    # 6) Metrics to UI
    if send:
        try:
            send("message", f"Métricas: cajas totales={total_boxes}")
            for p, cov in sorted(coverage_by_page.items()):
                send("message", f"  p.{p}: cobertura={cov:.1%}")
            send("message", f"Colocación figuras: ratio={placed_ratio}, fallback={placed_fallback}")
        except Exception:
            pass

    return EnrichResult(total_pages=len(page_images), total_boxes=total_boxes, images_written=tuple(copied))

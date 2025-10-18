"""Quick QA overlays and basic metrics for detected boxes.

This module provides utilities to:
  - Rasterize a given PDF page to PNG for detectors.
  - Run layout and table detectors, fuse and postprocess boxes.
  - Draw a semi-transparent overlay of math/table boxes on the page image.
  - Compute and persist basic metrics (coverage, box counts, timings).

It also exposes a small CLI for ad-hoc validation:

    python -m core.runner.qa --pdf Mastering.pdf --pages 67 76 --out tmp_selective --dpi 360

Outputs per page are written under ``<out>/<page>/``:
  - ``overlay-<page>.png``: page image with colored overlays
  - ``metrics-<page>.json``: coverage, counts, and timings
  - ``page-<page>-<page or 3-digit>.png``: rasterized page used by detectors
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:  # Pillow is optional at import-time; we guard usage in functions
    from PIL import Image, ImageDraw  # type: ignore
except Exception:  # pragma: no cover - optional import guard
    Image = None  # type: ignore[assignment]
    ImageDraw = None  # type: ignore[assignment]

from .layout import LayoutBox, LayoutResult, infer_layout_on_image
from .rasterize import rasterize_pdf_to_png
from .tables import TableBox, fuse_tables_with_layout, infer_tables_on_image
from .postprocess import PostprocessOptions, postprocess_math_and_tables
from .fallback import compute_coverage_for_boxes

__all__ = [
    "QAMetrics",
    "QAResult",
    "draw_overlay",
    "run_page_qa",
]


@dataclass(frozen=True)
class QAMetrics:
    page: int
    dpi: int
    coverage: float
    box_count: int
    box_count_math: int
    box_count_table: int
    time_layout_s: float
    time_tables_s: float
    time_postprocess_s: float
    time_total_s: float


@dataclass(frozen=True)
class QAResult:
    page: int
    page_image: Path
    overlay_image: Path
    metrics_json: Path
    boxes: Tuple[LayoutBox, ...]
    metrics: QAMetrics


def _ensure_pillow() -> None:
    if Image is None or ImageDraw is None:  # pragma: no cover - environment dependent
        raise RuntimeError("Pillow (PIL) no disponible para dibujar overlays.")


def _find_pdftocairo_png(prefix: Path, page: int) -> Path:
    """Return the actual output PNG path from a pdftocairo prefix and page.

    It checks for both ``<prefix>-<page>.png`` and ``<prefix>-<page:03d>.png``.
    """
    candidates = [
        prefix.parent / f"{prefix.name}-{page}.png",
        prefix.parent / f"{prefix.name}-{page:03d}.png",
    ]
    for c in candidates:
        if c.exists():
            return c
    # If not found, return the first candidate by convention
    return candidates[-1]


def draw_overlay(
    image_path: Path,
    boxes: Sequence[LayoutBox],
    out_path: Path,
    *,
    colors: Optional[Mapping[str, Tuple[int, int, int, int]]] = None,
    outline_px: int = 3,
) -> Path:
    """Draw semi-transparent overlays for boxes on top of an image.

    Args:
        image_path: Base image (PNG/JPEG) path.
        boxes: Sequence of LayoutBox in same pixel space as the image.
        out_path: Destination PNG path for the overlay result.
        colors: Mapping label -> RGBA tuple for fill/outline color. Alpha is used
            for the fill; the outline uses an opaque variant of the same color.
        outline_px: Outline thickness in pixels.
    Returns: Path to the written overlay image.
    """
    _ensure_pillow()
    image_path = Path(image_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    base = Image.open(image_path).convert("RGBA")  # type: ignore[union-attr]
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # Default colors for known labels
    palette: Dict[str, Tuple[int, int, int, int]] = {
        "mathblock": (255, 0, 0, 96),       # red, semi-transparent
        "tableblock": (0, 128, 255, 96),    # blue-ish
    }
    if colors:
        palette.update(colors)

    for b in boxes:
        rgba = palette.get(b.label, (255, 255, 0, 64))  # default: yellow
        x1, y1 = int(b.x), int(b.y)
        x2, y2 = int(b.x + b.width), int(b.y + b.height)
        # Filled rectangle (semi-transparent)
        draw.rectangle([x1, y1, x2, y2], fill=rgba)
        # Opaque outline: same color without alpha
        outline = (rgba[0], rgba[1], rgba[2], 255)
        for k in range(outline_px):
            draw.rectangle([x1 - k, y1 - k, x2 + k, y2 + k], outline=outline)

    combined = Image.alpha_composite(base, overlay)
    combined.save(out_path)
    try:
        base.close()
        overlay.close()
        combined.close()
    except Exception:
        pass
    return out_path


def run_page_qa(
    pdf_path: Path,
    page: int,
    out_dir: Path,
    *,
    dpi: int = 360,
    device: str = "cpu",
    paddleocr_path: Optional[str] = None,
) -> QAResult:
    """Run detectors and generate overlay/metrics for a single PDF page.

    Returns a QAResult with output paths and collected metrics.
    """
    pdf_path = Path(pdf_path)
    out_dir = Path(out_dir)
    page_dir = out_dir / str(int(page))
    page_dir.mkdir(parents=True, exist_ok=True)

    # 1) Rasterize page to PNG for detectors
    prefix = page_dir / f"page-{page}"
    rasterize_pdf_to_png(pdf_path, prefix, dpi=dpi, first_page=page, last_page=page)
    page_png = _find_pdftocairo_png(prefix, page)

    # 2) Run detectors and postprocess
    paddle_path = (
        paddleocr_path
        if paddleocr_path is not None
        else str((Path(__file__).resolve().parents[2] / ".venv" / "bin" / "paddleocr"))
    )

    t0 = time.perf_counter()
    layout = infer_layout_on_image(page_png, device=device, paddleocr_path=paddle_path)
    t1 = time.perf_counter()
    tables = infer_tables_on_image(page_png, device=device, paddleocr_path=paddle_path)
    t2 = time.perf_counter()
    fused = fuse_tables_with_layout(layout, tables, iou_threshold=0.3)
    post = postprocess_math_and_tables(
        fused, options=PostprocessOptions(min_area_px=150, dpi=dpi, margin_pts=5.0)
    )
    t3 = time.perf_counter()

    # 3) Draw overlay
    overlay_path = page_dir / f"overlay-{page}.png"
    draw_overlay(page_png, list(post.boxes), overlay_path)

    # 4) Metrics (coverage, counts, timings)
    if Image is None:  # pragma: no cover - defensive
        width = height = 0
    else:
        with Image.open(page_png) as im:  # type: ignore[union-attr]
            width, height = im.size

    coverage = compute_coverage_for_boxes((width, height), post.boxes, labels=("mathblock", "tableblock"))
    n_total = len(post.boxes)
    n_math = sum(1 for b in post.boxes if b.label == "mathblock")
    n_table = sum(1 for b in post.boxes if b.label == "tableblock")

    m = QAMetrics(
        page=int(page),
        dpi=int(dpi),
        coverage=float(coverage),
        box_count=int(n_total),
        box_count_math=int(n_math),
        box_count_table=int(n_table),
        time_layout_s=float(t1 - t0),
        time_tables_s=float(t2 - t1),
        time_postprocess_s=float(t3 - t2),
        time_total_s=float(t3 - t0),
    )

    metrics_path = page_dir / f"metrics-{page}.json"
    metrics_path.write_text(
        json.dumps(
            {
                "page": m.page,
                "dpi": m.dpi,
                "coverage": m.coverage,
                "box_count": m.box_count,
                "box_count_math": m.box_count_math,
                "box_count_table": m.box_count_table,
                "time_layout_s": m.time_layout_s,
                "time_tables_s": m.time_tables_s,
                "time_postprocess_s": m.time_postprocess_s,
                "time_total_s": m.time_total_s,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return QAResult(
        page=int(page),
        page_image=page_png,
        overlay_image=overlay_path,
        metrics_json=metrics_path,
        boxes=post.boxes,
        metrics=m,
    )


def _parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Genera overlays y métricas QA para páginas de un PDF")
    p.add_argument("--pdf", type=Path, required=True, help="Ruta al PDF de entrada")
    p.add_argument(
        "--pages",
        type=int,
        nargs="+",
        required=True,
        help="Números de página (1-based)",
    )
    p.add_argument("--out", type=Path, default=Path("tmp_selective"), help="Directorio de salida por página")
    p.add_argument("--dpi", type=int, default=360, help="DPI para rasterización y postproceso")
    p.add_argument("--device", type=str, default="cpu", help="Dispositivo para PaddleOCR (cpu/gpu:0)")
    p.add_argument(
        "--paddleocr",
        type=str,
        default=None,
        help="Ruta al binario paddleocr (por defecto .venv/bin/paddleocr)",
    )
    return p.parse_args(argv)


def _main(argv: Optional[Sequence[str]] = None) -> int:  # pragma: no cover - CLI utility
    args = _parse_args(argv)
    pdf = args.pdf
    pages: Sequence[int] = args.pages
    out = args.out
    dpi = int(args.dpi)
    paddle = args.paddleocr

    results: List[QAResult] = []
    for pg in pages:
        res = run_page_qa(pdf, pg, out, dpi=dpi, device=args.device, paddleocr_path=paddle)
        results.append(res)
        print(
            json.dumps(
                {
                    "page": res.page,
                    "overlay": str(res.overlay_image),
                    "metrics": json.loads(res.metrics_json.read_text(encoding="utf-8")),
                },
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI utility
    raise SystemExit(_main())


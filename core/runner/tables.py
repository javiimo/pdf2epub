"""Table detection helpers built on PaddleOCR table structure recognition.

This module invokes the ``paddleocr table_structure_recognition`` CLI to obtain
cell-level quadrilateral boxes and clusters them into table-level bounding
rectangles. It also provides utilities to fuse these detections with a prior
layout result (e.g., from PP-DocLayout) using an IoU threshold.
"""

from __future__ import annotations

import json
import math
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from .layout import LayoutBox, LayoutResult

__all__ = [
    "TableDetectError",
    "TableBox",
    "infer_tables_on_image",
    "fuse_tables_with_layout",
]


class TableDetectError(RuntimeError):
    """Raised when table detection fails or returns invalid data."""


@dataclass(frozen=True)
class TableBox:
    label: str  # always 'table'
    score: float
    x: int
    y: int
    width: int
    height: int


def _build_paddleocr_tsr_command(
    image_path: Path,
    *,
    save_path: Path,
    device: str = "cpu",
    paddleocr_path: str = "paddleocr",
) -> Sequence[str]:
    return [
        paddleocr_path,
        "table_structure_recognition",
        "-i",
        str(Path(image_path)),
        "--save_path",
        str(Path(save_path)),
        "--device",
        device,
    ]


def _rect_from_quad(quad: Sequence[float]) -> Tuple[int, int, int, int]:
    """Return axis-aligned rectangle (x, y, w, h) from a 4-point quad.

    Quad is ``[x1, y1, x2, y2, x3, y3, x4, y4]`` in image coordinates.
    """
    xs = [float(quad[i]) for i in range(0, 8, 2)]
    ys = [float(quad[i]) for i in range(1, 8, 2)]
    x1, y1 = min(xs), min(ys)
    x2, y2 = max(xs), max(ys)
    x, y = int(round(x1)), int(round(y1))
    w, h = max(0, int(round(x2 - x1))), max(0, int(round(y2 - y1)))
    return x, y, w, h


def _load_tsr_cells(json_path: Path) -> List[Tuple[int, int, int, int]]:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - delegated
        raise TableDetectError(f"No se pudo leer el resultado JSON: {json_path}: {exc}") from exc

    quads = data.get("bbox")
    if quads is None:
        raise TableDetectError("El JSON de TSR no contiene el campo 'bbox'.")

    rects: List[Tuple[int, int, int, int]] = []
    for entry in quads:
        try:
            rects.append(_rect_from_quad(entry))
        except Exception as exc:
            raise TableDetectError(f"Entrada de bbox inválida en {json_path}: {entry}") from exc
    return rects


def _expand_rect(rect: Tuple[int, int, int, int], margin: int) -> Tuple[int, int, int, int]:
    x, y, w, h = rect
    x2, y2 = x + w, y + h
    x, y = x - margin, y - margin
    x2, y2 = x2 + margin, y2 + margin
    w, h = max(0, x2 - x), max(0, y2 - y)
    return x, y, w, h


def _intersects(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return False
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    return not (ax2 <= bx or bx2 <= ax or ay2 <= by or by2 <= ay)


def _merge_rects_connected(rects: List[Tuple[int, int, int, int]], *, proximity: int = 20) -> List[Tuple[int, int, int, int]]:
    """Merge rectangles connected under a proximity margin.

    Two rectangles belong to the same group if their expanded versions (by
    ``proximity`` pixels on each side) intersect. Returns merged groups as
    bounding rectangles.
    """
    if not rects:
        return []

    expanded = [_expand_rect(r, proximity) for r in rects]
    n = len(rects)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[ri] = rj

    for i in range(n):
        for j in range(i + 1, n):
            if _intersects(expanded[i], expanded[j]):
                union(i, j)

    groups: dict[int, List[Tuple[int, int, int, int]]] = {}
    for idx, rect in enumerate(rects):
        root = find(idx)
        groups.setdefault(root, []).append(rect)

    merged: List[Tuple[int, int, int, int]] = []
    for members in groups.values():
        xs = [r[0] for r in members]
        ys = [r[1] for r in members]
        x2s = [r[0] + r[2] for r in members]
        y2s = [r[1] + r[3] for r in members]
        x1, y1 = min(xs), min(ys)
        x2, y2 = max(x2s), max(y2s)
        merged.append((x1, y1, max(0, x2 - x1), max(0, y2 - y1)))
    return merged


def infer_tables_on_image(
    image_path: Path,
    *,
    device: str = "cpu",
    paddleocr_path: str = "paddleocr",
    proximity: int = 20,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Tuple[TableBox, ...]:
    """Run table detection via TSR and return clustered table boxes.

    The PaddleOCR TSR model yields cell-level quads; we form coarse table
    bounding boxes by merging nearby cells. The returned boxes are labeled as
    ``'table'`` with a neutral score (1.0) since TSR JSON lacks per-table
    confidence.
    """
    image_path = Path(image_path)
    if not image_path.exists():
        raise TableDetectError(f"La imagen de entrada no existe: {image_path}")

    with tempfile.TemporaryDirectory(prefix="tsr-") as tmpdir:
        outdir = Path(tmpdir)
        command = _build_paddleocr_tsr_command(
            image_path, save_path=outdir, device=device, paddleocr_path=paddleocr_path
        )
        try:
            run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:  # pragma: no cover - delegated
            raise TableDetectError(
                f"paddleocr terminó con código {exc.returncode}: {exc.stderr or exc}"
            ) from exc
        except OSError as exc:
            raise TableDetectError(f"No se pudo ejecutar paddleocr: {exc}") from exc

        json_files = sorted(outdir.glob("*_res.json"))
        if not json_files:
            raise TableDetectError("No se encontró el archivo de resultados JSON de TSR.")

        rects = _load_tsr_cells(json_files[0])
        merged = _merge_rects_connected(rects, proximity=proximity)
        tables = [TableBox(label="table", score=1.0, x=x, y=y, width=w, height=h) for x, y, w, h in merged]
        return tuple(tables)


def _iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return 0.0
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = aw * ah
    area_b = bw * bh
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union


def _overlap_with_smaller(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    if aw <= 0 or ah <= 0 or bw <= 0 or bh <= 0:
        return 0.0
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    smaller = min(aw * ah, bw * bh)
    if smaller <= 0:
        return 0.0
    return inter / smaller


def fuse_tables_with_layout(
    layout: LayoutResult,
    tatr_tables: Sequence[TableBox],
    *,
    iou_threshold: float = 0.3,
) -> LayoutResult:
    """Fuse TSR-based table boxes with an existing layout result.

    Only TSR boxes with IoU >= ``iou_threshold`` against a layout 'table' box
    are considered. Fused boxes take the union of coordinates to be more
    inclusive. Other layout boxes (e.g., 'formula') are preserved untouched.
    """
    layout_tables: List[LayoutBox] = [b for b in layout.boxes if b.label == "table"]
    others: List[LayoutBox] = [b for b in layout.boxes if b.label != "table"]

    fused_tables: List[LayoutBox] = []
    used = [False] * len(layout_tables)

    for t in tatr_tables:
        t_rect = (t.x, t.y, t.width, t.height)
        best_score = 0.0
        best_idx = -1
        for i, l in enumerate(layout_tables):
            l_rect = (l.x, l.y, l.width, l.height)
            iou = _iou(t_rect, l_rect)
            overlap_smaller = 0.0
            t_area = t.width * t.height
            l_area = l.width * l.height
            if t_area > 0 and l_area > 0 and t_area <= l_area * 1.05:
                overlap_smaller = _overlap_with_smaller(t_rect, l_rect)
            overlap = max(iou, overlap_smaller)
            if overlap > best_score:
                best_score = overlap
                best_idx = i
        if best_score >= iou_threshold and best_idx >= 0:
            l = layout_tables[best_idx]
            used[best_idx] = True
            # Union rectangle
            x1 = min(l.x, t.x)
            y1 = min(l.y, t.y)
            x2 = max(l.x + l.width, t.x + t.width)
            y2 = max(l.y + l.height, t.y + t.height)
            fused_tables.append(
                LayoutBox(label="table", score=max(l.score, t.score), x=int(x1), y=int(y1), width=int(max(0, x2 - x1)), height=int(max(0, y2 - y1)))
            )

    # Keep any layout table without a matching TSR box
    for i, l in enumerate(layout_tables):
        if not used[i]:
            fused_tables.append(l)

    # Preserve order: others first (as in original order), then tables (could be grouped)
    final_boxes: Tuple[LayoutBox, ...] = tuple(others + fused_tables)
    return LayoutResult(image_path=layout.image_path, page_index=layout.page_index, boxes=final_boxes)


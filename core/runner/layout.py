"""Layout detection helpers using PaddleOCR's layout_detection.

This module provides a small wrapper around the `paddleocr layout_detection`
CLI to infer document layout regions (e.g., text, formula, table) from a page
image. Results are returned as pixel-based bounding boxes suitable for
subsequent selective rasterization or HTML insertion.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "LayoutError",
    "LayoutBox",
    "LayoutResult",
    "infer_layout_on_image",
]


class LayoutError(RuntimeError):
    """Raised when layout detection fails or returns invalid data."""


@dataclass(frozen=True)
class LayoutBox:
    """Single layout bounding box in pixel coordinates.

    Coordinates are expressed as integers in the image pixel space. The
    coordinates represent the top-left corner (x, y) and the size (width,
    height). The bounding rectangle corresponds to the inclusive range from
    (x, y) to (x + width, y + height).
    """

    label: str
    score: float
    x: int
    y: int
    width: int
    height: int


@dataclass(frozen=True)
class LayoutResult:
    """Structured result for a single image layout inference."""

    image_path: Path
    page_index: Optional[int]
    boxes: Tuple[LayoutBox, ...]


def _build_paddleocr_layout_command(
    image_path: Path,
    *,
    save_path: Path,
    threshold: float = 0.3,
    device: str = "cpu",
    paddleocr_path: str = "paddleocr",
) -> Sequence[str]:
    return [
        paddleocr_path,
        "layout_detection",
        "-i",
        str(Path(image_path)),
        "--save_path",
        str(Path(save_path)),
        "--device",
        device,
        "--threshold",
        str(float(threshold)),
    ]


def _parse_result_json(json_path: Path) -> LayoutResult:
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - delegated
        raise LayoutError(f"No se pudo leer el resultado JSON: {json_path}: {exc}") from exc

    input_path = Path(data.get("input_path") or "")
    page_index = data.get("page_index")
    raw_boxes = data.get("boxes")
    if not input_path or raw_boxes is None:
        raise LayoutError("El resultado de layout no contiene los campos esperados (boxes/input_path).")

    boxes: List[LayoutBox] = []
    for entry in raw_boxes:
        try:
            label = str(entry["label"])  # e.g., 'formula', 'table', 'text'
            score = float(entry.get("score", 0.0))
            x1, y1, x2, y2 = map(float, entry["coordinate"])  # [x1, y1, x2, y2]
            x, y = int(round(x1)), int(round(y1))
            w, h = max(0, int(round(x2 - x1))), max(0, int(round(y2 - y1)))
        except Exception as exc:
            raise LayoutError(f"Entrada de bbox inválida en {json_path}: {entry}") from exc
        boxes.append(LayoutBox(label=label, score=score, x=x, y=y, width=w, height=h))

    return LayoutResult(image_path=input_path, page_index=page_index, boxes=tuple(boxes))


def infer_layout_on_image(
    image_path: Path,
    *,
    threshold: float = 0.3,
    device: str = "cpu",
    paddleocr_path: str = "paddleocr",
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> LayoutResult:
    """Run layout detection on a single page image and return pixel bboxes.

    This function invokes the PaddleOCR CLI with the official PP-DocLayout
    model. It writes a JSON file with results to a temporary directory, then
    parses and returns the data in a friendly structure.

    Args:
        image_path: Path to the input page image (PNG/JPEG).
        threshold: Minimum confidence score for predictions to be included.
        device: Inference device (e.g., 'cpu', 'gpu', 'gpu:0').
        paddleocr_path: Executable name or path for the paddleocr CLI.
        run: Optional callable compatible with ``subprocess.run`` to execute
            the CLI. Allows dependency injection for tests.

    Returns:
        LayoutResult with per-class boxes in pixel coordinates.
    """
    image_path = Path(image_path)
    if not image_path.exists():
        raise LayoutError(f"La imagen de entrada no existe: {image_path}")

    with tempfile.TemporaryDirectory(prefix="layout-") as tmpdir:
        outdir = Path(tmpdir)
        command = _build_paddleocr_layout_command(
            image_path, save_path=outdir, threshold=threshold, device=device, paddleocr_path=paddleocr_path
        )
        try:
            completed = run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:  # pragma: no cover - delegated
            raise LayoutError(
                f"paddleocr terminó con código {exc.returncode}: {exc.stderr or exc}"
            ) from exc
        except OSError as exc:
            raise LayoutError(f"No se pudo ejecutar paddleocr: {exc}") from exc

        # The CLI writes a single *_res.json file in the output directory
        json_files = sorted(outdir.glob("*_res.json"))
        if not json_files:
            # If nothing was saved, include stdout/stderr for hints
            raise LayoutError(
                "No se encontró el archivo de resultados JSON del layout.")

        return _parse_result_json(json_files[0])


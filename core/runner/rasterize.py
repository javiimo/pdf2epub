"""Rasterization helpers using pdftocairo.

This module provides a small wrapper around the pdftocairo CLI to render
PDF pages into PNG images at a given DPI. It optionally supports cropping
via the ``-x -y -W -H`` flags exposed by pdftocairo.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

__all__ = [
    "CropRect",
    "RasterizeError",
    "build_pdftocairo_png_command",
    "rasterize_pdf_to_png",
    "rasterize_boxes_from_pdf",
]


@dataclass(frozen=True)
class CropRect:
    """Crop rectangle in pixels for image outputs.

    Coordinates follow pdftocairo's convention where ``x`` and ``y`` refer to
    the top-left corner of the crop region. ``width`` and ``height`` specify
    the cropped area size in pixels at the selected DPI.
    """

    x: int
    y: int
    width: int
    height: int


class RasterizeError(RuntimeError):
    """Raised when pdftocairo fails to render pages."""


def build_pdftocairo_png_command(
    input_pdf: Path,
    output_prefix: Path,
    *,
    dpi: int = 360,
    crop: Optional[CropRect] = None,
    first_page: Optional[int] = None,
    last_page: Optional[int] = None,
    pdftocairo_path: str = "pdftocairo",
    singlefile: bool = False,
) -> Sequence[str]:
    """Build the pdftocairo command to render PNG images.

    The output name is treated as a prefix; pdftocairo will append page numbers
    (e.g. ``<prefix>-1.png, <prefix>-2.png``). ``first_page`` and ``last_page``
    restrict the page range. When ``crop`` is provided, the corresponding area
    is rendered instead of full pages.
    """
    args: list[str] = [pdftocairo_path, "-png", "-r", str(int(dpi))]

    if first_page is not None:
        args += ["-f", str(int(first_page))]
    if last_page is not None:
        args += ["-l", str(int(last_page))]

    if crop is not None:
        args += [
            "-x",
            str(int(crop.x)),
            "-y",
            str(int(crop.y)),
            "-W",
            str(int(crop.width)),
            "-H",
            str(int(crop.height)),
        ]

    if singlefile:
        args.append("-singlefile")

    args += [str(input_pdf), str(output_prefix)]
    return args


def rasterize_pdf_to_png(
    input_pdf: Path,
    output_prefix: Path,
    *,
    dpi: int = 360,
    crop: Optional[CropRect] = None,
    first_page: Optional[int] = None,
    last_page: Optional[int] = None,
    pdftocairo_path: str = "pdftocairo",
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Path:
    """Render one or more PDF pages to PNG images using pdftocairo.

    Ensures the output directory exists and invokes pdftocairo with ``check=True``
    so failures raise ``subprocess.CalledProcessError``. Any execution problem is
    wrapped into a ``RasterizeError``.

    Returns the effective output prefix (``<prefix>-N.png`` will be produced).
    """
    input_pdf = Path(input_pdf)
    output_prefix = Path(output_prefix)
    output_prefix.parent.mkdir(parents=True, exist_ok=True)

    command = build_pdftocairo_png_command(
        input_pdf,
        output_prefix,
        dpi=dpi,
        crop=crop,
        first_page=first_page,
        last_page=last_page,
        pdftocairo_path=pdftocairo_path,
    )

    try:
        run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:  # pragma: no cover - delegated
        raise RasterizeError(
            f"pdftocairo terminó con código {exc.returncode}: {exc.stderr or exc}"
        ) from exc
    except OSError as exc:
        raise RasterizeError(f"No se pudo ejecutar pdftocairo: {exc}") from exc

    return output_prefix


def rasterize_boxes_from_pdf(
    input_pdf: Path,
    output_dir: Path,
    page: int,
    boxes: Sequence["LayoutBox"],
    *,
    dpi: int = 360,
    labels: Optional[Sequence[str]] = ("mathblock", "tableblock"),
    image_format: str = "png",
    jpeg_quality: int = 85,
    pdftocairo_path: str = "pdftocairo",
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Tuple[Path, ...]:
    """Rasterize each provided bbox on a given PDF page to individual PNGs.

    Uses pdftocairo selective crop flags (x, y, W, H) at the specified DPI and
    writes one image per box with a stable name pattern: ``part-p%04d-b%02d.png``.

    Args:
        input_pdf: Source PDF path.
        output_dir: Directory where cropped images will be saved.
        page: 1-based page index in the PDF to render.
        boxes: Sequence of LayoutBox in pixel coordinates at the target DPI.
        dpi: Rasterization DPI (must match the coordinates' pixel space).
        labels: Optional list of labels to include; set to None to include all.
        pdftocairo_path: Executable name or path for pdftocairo.
        run: Subprocess runner, injectable for tests.

    Returns:
        Tuple with the paths to the generated images, ordered by input boxes.
    """
    input_pdf = Path(input_pdf)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Lazy import to avoid circular dependency at module import time
    try:
        from .layout import LayoutBox  # type: ignore
    except Exception:  # pragma: no cover - defensive fallback for typing only
        LayoutBox = object  # type: ignore

    outputs: list[Path] = []

    def _clip_to_positive(x: int, y: int, w: int, h: int) -> Tuple[int, int, int, int]:
        x2, y2 = x + max(0, w), y + max(0, h)
        # Ensure strictly positive width/height after clipping to non-negative origin
        cx = max(0, x)
        cy = max(0, y)
        cw = max(1, x2 - cx)
        ch = max(1, y2 - cy)
        return cx, cy, cw, ch

    # Filter boxes by label if requested
    use_boxes = [b for b in boxes if (labels is None or b.label in labels)]

    fmt = (image_format or "png").strip().lower()
    if fmt not in ("png", "jpeg", "jpg"):
        fmt = "png"
    if fmt == "jpg":
        fmt = "jpeg"

    for idx, b in enumerate(use_boxes, start=1):
        bx, by, bw, bh = _clip_to_positive(int(b.x), int(b.y), int(b.width), int(b.height))
        crop = CropRect(x=bx, y=by, width=bw, height=bh)
        # Build output stem without extension; pdftocairo adds the .png suffix even with -singlefile
        out_stem = output_dir / f"part-p{int(page):04d}-b{idx:02d}"
        if fmt == "png":
            command = build_pdftocairo_png_command(
                input_pdf,
                out_stem,
                dpi=dpi,
                crop=crop,
                first_page=page,
                last_page=page,
                pdftocairo_path=pdftocairo_path,
                singlefile=True,
            )
        else:
            # Build JPEG command: -jpeg with optional -jpegopt quality=<n>
            command = [pdftocairo_path, "-jpeg", "-r", str(int(dpi))]
            command += ["-f", str(int(page)), "-l", str(int(page))]
            if crop is not None:
                command += [
                    "-x",
                    str(int(crop.x)),
                    "-y",
                    str(int(crop.y)),
                    "-W",
                    str(int(crop.width)),
                    "-H",
                    str(int(crop.height)),
                ]
            command.append("-singlefile")
            q = max(40, min(100, int(jpeg_quality)))
            command += ["-jpegopt", f"quality={q}"]
            command += [str(input_pdf), str(out_stem)]

        try:
            run(command, check=True, capture_output=True, text=True)
        except subprocess.CalledProcessError as exc:  # pragma: no cover - delegated
            raise RasterizeError(
                f"pdftocairo terminó con código {exc.returncode}: {exc.stderr or exc}"
            ) from exc
        except OSError as exc:
            raise RasterizeError(f"No se pudo ejecutar pdftocairo: {exc}") from exc
        outputs.append(out_stem.with_suffix('.png' if fmt == "png" else '.jpg'))

    return tuple(outputs)

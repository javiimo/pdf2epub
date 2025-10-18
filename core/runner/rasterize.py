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


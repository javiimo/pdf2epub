"""Assemble helpers to inject selective raster images into an OEB directory.

This module performs three tasks:
  1) Copy provided images into the OEB tree (Images/ by default).
  2) Update content.opf <manifest> with <item> entries for these images.
  3) Insert <figure><img ...></figure> blocks into a given spine HTML.

Insertion strategy is intentionally simple for a first iteration: append
figures before </body>. Future iterations may use fuzzy matching to replace
the containing <p> when the HTML doesn't preserve page boundaries.
"""

from __future__ import annotations

import os
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Mapping, Optional, Sequence, Tuple

__all__ = [
    "AssemblyError",
    "copy_images_into_oeb",
    "add_images_to_manifest",
    "insert_figures_into_html",
]


class AssemblyError(RuntimeError):
    """Raised when OEB assembly operations fail."""


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def copy_images_into_oeb(
    oeb_root: Path,
    images: Sequence[Path],
    *,
    target_dir: str = "Images",
) -> Tuple[Path, ...]:
    """Copy images into OEB under ``target_dir`` and return their new paths.

    The target_dir is relative to the OEB root. Existing files are overwritten.
    """
    oeb_root = Path(oeb_root)
    dest_dir = oeb_root / target_dir
    _ensure_dir(dest_dir)

    outputs: List[Path] = []
    for src in images:
        src = Path(src)
        if not src.exists():
            raise AssemblyError(f"La imagen de entrada no existe: {src}")
        dst = dest_dir / src.name
        shutil.copy2(src, dst)
        outputs.append(dst)
    return tuple(outputs)


def _qualified(tag: str, namespace: Optional[str]) -> str:
    return f"{{{namespace}}}{tag}" if namespace else tag


def _detect_namespace(tag: str) -> Optional[str]:
    if tag.startswith("{") and "}" in tag:
        return tag[1 : tag.index("}")]
    return None


def add_images_to_manifest(opf_path: Path, image_paths: Sequence[Path]) -> None:
    """Add <item> entries for images to OPF manifest if missing.

    - Uses id="img-<stem>" (disambiguated with a numeric suffix if needed).
    - href is relative to OPF directory.
    - media-type is derived from suffix; supports PNG/JPEG/SVG minimally.
    """
    opf_path = Path(opf_path)
    if not opf_path.exists():
        raise AssemblyError(f"No se encontró OPF: {opf_path}")

    try:
        tree = ET.parse(opf_path)
    except (ET.ParseError, OSError) as exc:  # pragma: no cover - delegated
        raise AssemblyError(f"No se pudo leer '{opf_path.name}': {exc}") from exc

    root = tree.getroot()
    ns = _detect_namespace(root.tag)
    manifest = root.find(_qualified("manifest", ns))
    if manifest is None:
        raise AssemblyError("El archivo OPF no contiene <manifest>.")

    existing_ids = {item.get("id") for item in manifest.findall(_qualified("item", ns))}
    existing_hrefs = {item.get("href") for item in manifest.findall(_qualified("item", ns))}

    def _media_type(path: Path) -> str:
        ext = path.suffix.lower()
        if ext in (".png",):
            return "image/png"
        if ext in (".jpg", ".jpeg"):
            return "image/jpeg"
        if ext == ".svg":
            return "image/svg+xml"
        # fallback
        return "application/octet-stream"

    for img in image_paths:
        img = Path(img)
        # Make href relative to OPF file
        rel = os.path.relpath(img, opf_path.parent)
        if rel in existing_hrefs:
            continue
        base_id = f"img-{img.stem}"
        new_id = base_id
        suffix = 1
        while new_id in existing_ids:
            suffix += 1
            new_id = f"{base_id}-{suffix}"
        item = ET.Element(_qualified("item", ns))
        item.set("id", new_id)
        item.set("href", rel.replace("\\", "/"))
        item.set("media-type", _media_type(img))
        manifest.append(item)
        existing_ids.add(new_id)
        existing_hrefs.add(rel)

    # Write back preserving the XML declaration
    tree.write(opf_path, encoding="utf-8", xml_declaration=True)


@dataclass(frozen=True)
class FigureSpec:
    """Descriptor for an image insertion into HTML."""

    image_path: Path  # absolute path inside OEB tree
    label: str  # e.g., "mathblock" or "tableblock"


def _build_figure_html(rel_src: str, label: str) -> str:
    # For mathblock → <figure><img class="mathblock" src="..."/></figure>
    # For tableblock → <figure class="table"><img class="tableblock" src="..."/></figure>
    label = (label or "").strip().lower()
    if label == "tableblock":
        return f'<figure class="table"><img class="tableblock" src="{rel_src}"/></figure>'
    return f'<figure><img class="mathblock" src="{rel_src}"/></figure>'


def insert_figures_into_html(
    html_path: Path,
    figures: Sequence[FigureSpec],
) -> None:
    """Append figure nodes before </body> in the given XHTML/HTML file.

    Falls back to appending at end of file if </body> is not found.
    """
    html_path = Path(html_path)
    if not html_path.exists():
        raise AssemblyError(f"HTML no encontrado: {html_path}")

    text = html_path.read_text(encoding="utf-8", errors="ignore")
    insertion = []
    for spec in figures:
        rel_src = os.path.relpath(spec.image_path, html_path.parent).replace("\\", "/")
        insertion.append(_build_figure_html(rel_src, spec.label))
    snippet = "\n" + "\n".join(insertion) + "\n"

    # Try case-insensitive search for </body>
    lower = text.lower()
    idx = lower.rfind("</body>")
    if idx != -1:
        new_text = text[:idx] + snippet + text[idx:]
    else:
        # Fallback: append before </html> if present
        idx_html = lower.rfind("</html>")
        if idx_html != -1:
            new_text = text[:idx_html] + snippet + text[idx_html:]
        else:
            new_text = text + snippet

    html_path.write_text(new_text, encoding="utf-8")


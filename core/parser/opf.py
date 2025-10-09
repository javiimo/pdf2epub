"""Helpers to inspect OPF files and extract spine information."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = ["OpfParserError", "SpineItem", "parse_spine", "find_first_spine_html"]


class OpfParserError(RuntimeError):
    """Raised when an OPF document cannot be parsed or is inconsistent."""


@dataclass(frozen=True)
class SpineItem:
    """Represents a single spine entry resolved against the manifest."""

    idref: str
    href: Path
    media_type: str
    linear: bool


def _qualified(tag: str, namespace: Optional[str]) -> str:
    if namespace:
        return f"{{{namespace}}}{tag}"
    return tag


def _detect_namespace(tag: str) -> Optional[str]:
    if tag.startswith("{") and "}" in tag:
        return tag[1 : tag.index("}")]
    return None


def _load_xml(path: Path) -> ET.Element:
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError) as exc:
        raise OpfParserError(f"No se pudo leer '{path.name}': {exc}") from exc
    return tree.getroot()


def _build_manifest(root: ET.Element, namespace: Optional[str]) -> Dict[str, Tuple[str, str]]:
    manifest = root.find(_qualified("manifest", namespace))
    if manifest is None:
        raise OpfParserError("El archivo OPF no contiene <manifest>.")

    mapping: Dict[str, Tuple[str, str]] = {}
    for item in manifest.findall(_qualified("item", namespace)):
        item_id = item.get("id")
        href = item.get("href")
        media_type = item.get("media-type")
        if not item_id or not href or not media_type:
            raise OpfParserError("Elemento <item> incompleto en <manifest>.")
        mapping[item_id] = (href, media_type)
    return mapping


def _html_media_type(media_type: str) -> bool:
    mt = media_type.lower()
    return "html" in mt


def parse_spine(oeb_root: Path) -> List[SpineItem]:
    """Parse content.opf and return resolved spine entries."""
    opf_path = Path(oeb_root) / "content.opf"
    if not opf_path.exists():
        raise OpfParserError(f"No se encontró '{opf_path}'.")

    root = _load_xml(opf_path)
    namespace = _detect_namespace(root.tag)
    manifest_map = _build_manifest(root, namespace)

    spine = root.find(_qualified("spine", namespace))
    if spine is None:
        raise OpfParserError("El archivo OPF no contiene <spine>.")

    entries: List[SpineItem] = []
    for itemref in spine.findall(_qualified("itemref", namespace)):
        idref = itemref.get("idref")
        if not idref:
            raise OpfParserError("Elemento <itemref> sin atributo idref.")
        try:
            href, media_type = manifest_map[idref]
        except KeyError as exc:
            raise OpfParserError(f"Elemento <itemref> referencia id '{idref}' inexistente.") from exc

        linear_attr = itemref.get("linear", "yes").strip().lower()
        is_linear = linear_attr != "no"

        resolved = (opf_path.parent / Path(href)).resolve()
        entries.append(
            SpineItem(
                idref=idref,
                href=resolved,
                media_type=media_type,
                linear=is_linear,
            )
        )
    return entries


def find_first_spine_html(oeb_root: Path) -> Path:
    """Return the first spine HTML/XHTML document."""
    entries = parse_spine(oeb_root)
    for entry in entries:
        if not entry.linear:
            continue
        if not _html_media_type(entry.media_type):
            continue
        if not entry.href.exists():
            raise OpfParserError(f"El archivo referenciado por '{entry.idref}' no existe: {entry.href}")
        return entry.href
    raise OpfParserError("No se encontró ningún elemento HTML lineal en el spine.")

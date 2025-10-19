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
    "insert_figures_inline",
    "write_css_into_oeb",
    "add_css_to_manifest",
    "link_stylesheet_in_html",
    "install_default_css",
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


def write_css_into_oeb(
    oeb_root: Path,
    css_text: str,
    *,
    relative_path: str = "Styles/pdf2epub.css",
) -> Path:
    """Write a CSS file under the OEB tree and return its absolute path.

    The CSS file is created (or overwritten) at ``oeb_root/relative_path``.
    Parent directories are created as needed.
    """
    oeb_root = Path(oeb_root)
    css_path = oeb_root / relative_path
    _ensure_dir(css_path.parent)
    css_path.write_text(css_text, encoding="utf-8")
    return css_path


def add_css_to_manifest(opf_path: Path, css_path: Path) -> None:
    """Ensure a CSS <item> exists in the OPF manifest for the given file.

    - Uses id="css-<stem>" (deduplicated with numeric suffix if needed).
    - href is written relative to the OPF directory with forward slashes.
    - media-type is "text/css".
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

    css_path = Path(css_path)
    rel = os.path.relpath(css_path, opf_path.parent).replace("\\", "/")
    if rel in existing_hrefs:
        # Already present; nothing to do
        return

    base_id = f"css-{css_path.stem}"
    new_id = base_id
    suffix = 1
    while new_id in existing_ids:
        suffix += 1
        new_id = f"{base_id}-{suffix}"

    item = ET.Element(_qualified("item", ns))
    item.set("id", new_id)
    item.set("href", rel)
    item.set("media-type", "text/css")
    manifest.append(item)

    tree.write(opf_path, encoding="utf-8", xml_declaration=True)


def link_stylesheet_in_html(html_path: Path, css_path: Path) -> None:
    """Insert a <link rel="stylesheet"> pointing to css_path into the HTML.

    Prefer inserting before </head>. If <head> is missing, inject a <head> block
    right after <html ...> opening tag. As a last resort, prepend a <head>.
    The link is skipped if the file already references the css_path.
    """
    html_path = Path(html_path)
    if not html_path.exists():
        raise AssemblyError(f"HTML no encontrado: {html_path}")

    try:
        text = html_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:  # pragma: no cover - delegated
        raise AssemblyError(f"No se pudo leer HTML: {html_path}: {exc}") from exc

    rel_href = os.path.relpath(Path(css_path), html_path.parent).replace("\\", "/")
    link_tag = f'<link rel="stylesheet" type="text/css" href="{rel_href}" />'

    # Skip if already linked (rough check by href substring)
    lower = text.lower()
    if rel_href.lower() in lower:
        return

    # Insert before </head> if present
    end_head_idx = lower.find("</head>")
    if end_head_idx != -1:
        new_text = text[:end_head_idx] + link_tag + "\n" + text[end_head_idx:]
        html_path.write_text(new_text, encoding="utf-8")
        return

    # Otherwise, try to inject a <head> after <html ...>
    html_open_idx = lower.find("<html")
    if html_open_idx != -1:
        # Find the closing '>' of the <html ...> tag
        gt_idx = lower.find(">", html_open_idx)
        if gt_idx != -1:
            head_block = f"\n<head>\n{link_tag}\n</head>\n"
            new_text = text[: gt_idx + 1] + head_block + text[gt_idx + 1 :]
            html_path.write_text(new_text, encoding="utf-8")
            return

    # Fallback: prepend a head block
    new_text = f"<head>\n{link_tag}\n</head>\n" + text
    html_path.write_text(new_text, encoding="utf-8")


def install_default_css(
    oeb_root: Path,
    *,
    css_text: Optional[str] = None,
    opf_path: Optional[Path] = None,
    link_spine_html: bool = True,
) -> Path:
    """Install and link the default CSS rules into an OEB directory.

    - Writes CSS to ``Styles/pdf2epub.css`` under the OEB root.
    - Ensures an OPF manifest entry exists.
    - Optionally links the stylesheet in all linear spine HTML files.

    Returns the absolute path to the written CSS file.
    """
    rules = (
        css_text
        if css_text is not None
        else ".mathblock,.table{max-width:100%;height:auto;display:block;margin:0.6em auto;page-break-inside:avoid}."
    )
    css_abs = write_css_into_oeb(oeb_root, rules, relative_path="Styles/pdf2epub.css")

    opf = opf_path if opf_path is not None else Path(oeb_root) / "content.opf"
    add_css_to_manifest(opf, css_abs)

    if link_spine_html:
        try:
            # Import lazily to avoid an unconditional dependency
            from core.parser import parse_spine

            for entry in parse_spine(oeb_root):
                if entry.linear and "html" in entry.media_type.lower():
                    link_stylesheet_in_html(entry.href, css_abs)
        except Exception:
            # Linking is a best-effort enhancement; do not fail hard here.
            pass

    return css_abs


@dataclass(frozen=True)
class FigureSpec:
    """Descriptor for an image insertion into HTML.

    Optional placement hints allow inline insertion by page segment:
    - page_index: 1-based page number in the subset PDF.
    - y: Top pixel coordinate of the region on the page image.
    - page_height: Total pixel height of the page image used for detection.
    When any of these hints are missing, append-before-</body> fallback applies.
    """

    image_path: Path  # absolute path inside OEB tree
    label: str  # e.g., "mathblock" or "tableblock"
    page_index: int | None = None
    y: int | None = None
    page_height: int | None = None


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


def _find_pagebreaks(text: str) -> List[int]:
    """Return end indices of probable page-break markers within HTML.

    Heuristics cover common calibre markers and epub:type pagebreak. Returned
    positions are the end of the tag to make segment slicing intuitive.
    """
    import re

    patterns = [
        r"<a[^>]+(?:id|name)=(?:\"|')calibre_pb_\d+(?:\"|')[^>]*>\s*</a>",
        r"<[^>]+epub:type=(?:\"|')pagebreak(?:\"|')[^>]*>",
        r"<hr[^>]*class=(?:\"|')[^\"']*pagebreak[^\"']*(?:\"|')[^>]*>",
        r"<span[^>]*class=(?:\"|')[^\"']*pagebreak[^\"']*(?:\"|')[^>]*>\s*</span>",
    ]
    breaks: List[int] = []
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE | re.DOTALL):
            breaks.append(m.end())
    breaks.sort()
    # Deduplicate near-equal indices
    out: List[int] = []
    last = -1
    for b in breaks:
        if last == -1 or (b - last) > 1:
            out.append(b)
            last = b
    return out


def _segment_ranges(text: str, breaks: Sequence[int]) -> List[tuple[int, int]]:
    if not breaks:
        return [(0, len(text))]
    segs: List[tuple[int, int]] = []
    start = 0
    for b in breaks:
        segs.append((start, b))
        start = b
    segs.append((start, len(text)))
    return segs


def _find_block_end_positions(text: str, start: int, end: int) -> List[int]:
    """Return candidate insertion positions inside [start, end) after blocks.

    We consider the end of common block-level closing tags as insertion points.
    """
    import re

    region = text[start:end]
    positions: List[int] = []
    for m in re.finditer(r"</(p|div|li|table|figure|section|article|pre|h[1-6])>", region, re.IGNORECASE):
        positions.append(start + m.end())
    # Also consider <br> and <hr> as soft boundaries
    for m in re.finditer(r"<(br|hr)\b[^>]*>", region, re.IGNORECASE):
        positions.append(start + m.end())
    positions.sort()
    return positions


def _choose_insertion_index(candidates: Sequence[int], ratio: float, segment_end: int) -> int:
    if not candidates:
        return segment_end
    r = max(0.0, min(1.0, float(ratio)))
    idx = int(round(r * (len(candidates) - 1)))
    return candidates[idx]


def _build_marked_figure(rel_src: str, label: str, add_markers: bool) -> str:
    html = _build_figure_html(rel_src, label)
    if not add_markers:
        return html
    return f"<!--pdf2epub:fig:start-->{html}<!--pdf2epub:fig:end-->"


def _remove_nearby_math_text(text: str) -> str:
    """Heuristically remove short math-like paragraphs next to inserted figures.

    Looks for paragraphs immediately before or after our markers, and removes
    those whose plain text resembles a standalone formula (very short, uses
    math operators or super/subscripts). Conservative on length to avoid over-
    deletion. Finally removes the markers themselves.
    """
    import re

    def _strip_tags(s: str) -> str:
        return re.sub(r"<[^>]+>", "", s)

    def _looks_like_formula(txt: str) -> bool:
        t = txt.strip()
        if not t:
            return False
        # Short and contains math-y symbols
        if len(t) <= 60 and re.search(r"[=<>±×÷∑∫√^_()\[\]{}]", t):
            return True
        # Common inline math patterns like x^2, H_0
        if re.search(r"[A-Za-z]\s*[\^_]\s*\d+", t):
            return True
        return False

    # Remove <p> right before marker within the provided string
    def _remove_before(m: re.Match, current: str) -> str:
        start = m.start()
        prefix = current[:start]
        m_p = re.search(r"<p[^>]*>(.*?)</p>\s*$", prefix, re.IGNORECASE | re.DOTALL)
        if m_p:
            content = _strip_tags(m_p.group(1))
            # Do not cross pagebreak boundaries: only remove if the <p> lies
            # within the same page segment as the marker.
            breaks = _find_pagebreaks(current)
            prev_break = 0
            for b in breaks:
                if b <= start:
                    prev_break = b
                else:
                    break
            if _looks_like_formula(content) and m_p.start() >= prev_break:
                return current[: m_p.start()] + current[m.start():]
        return current

    # Remove <p> right after marker
    def _remove_after(m: re.Match, current: str) -> str:
        tail = current[m.end():]
        m_p = re.match(r"\s*<p[^>]*>(.*?)</p>", tail, re.IGNORECASE | re.DOTALL)
        if m_p:
            content = _strip_tags(m_p.group(1))
            # Respect the next pagebreak boundary
            breaks = _find_pagebreaks(current)
            next_break = len(current)
            for b in breaks:
                if b > m.end():
                    next_break = b
                    break
            if _looks_like_formula(content) and (m.end() + m_p.end()) <= next_break:
                return current[: m.end()] + current[m.end() + m_p.end():]
        return current

    # Regex-based removal of adjacent math-like paragraphs to avoid index drift
    cur = text
    start_pat = r"<!--pdf2epub:fig:start-->"
    end_pat = r"<!--pdf2epub:fig:end-->"

    # Remove a math-like paragraph immediately BEFORE the figure marker
    import functools

    def repl_before(m: re.Match) -> str:
        p_html = m.group("p")
        content = _strip_tags(p_html)
        fig = m.group("fig")
        if _looks_like_formula(content):
            return fig
        return m.group(0)

    before_re = re.compile(
        rf"(?P<p><p[^>]*>.*?</p>)\s*(?P<fig>{start_pat}.*?{end_pat})",
        re.IGNORECASE | re.DOTALL,
    )
    cur = before_re.sub(repl_before, cur)

    # Remove a math-like paragraph immediately AFTER the figure marker
    def repl_after(m: re.Match) -> str:
        fig = m.group("fig")
        p_html = m.group("p")
        content = _strip_tags(p_html)
        if _looks_like_formula(content):
            return fig
        return m.group(0)

    after_re = re.compile(
        rf"(?P<fig>{start_pat}.*?{end_pat})\s*(?P<p><p[^>]*>.*?</p>)",
        re.IGNORECASE | re.DOTALL,
    )
    cur = after_re.sub(repl_after, cur)

    # Drop markers
    cur = re.sub(r"<!--pdf2epub:fig:(?:start|end)-->", "", cur)
    return cur


def insert_figures_inline(
    html_path: Path,
    figures: Sequence[FigureSpec],
    *,
    page_offset: int = 1,
    remove_math_text: bool = True,
) -> None:
    """Insert figures inline into the HTML using page break heuristics.

    - Splits the document by detected page-break markers into segments.
    - Maps each FigureSpec to its page segment using ``page_index`` and
      ``page_offset``. Within a segment, places the figure after a block-level
      boundary according to its vertical ratio ``y/page_height``.
    - Falls back to appending at the end when placement hints are missing.
    """
    html_path = Path(html_path)
    if not html_path.exists():
        raise AssemblyError(f"HTML no encontrado: {html_path}")

    text = html_path.read_text(encoding="utf-8", errors="ignore")

    # If no usable hints, reuse the simple appending strategy
    if not any((f.page_index and f.y is not None and f.page_height) for f in figures):
        insert_figures_into_html(html_path, figures)
        return

    breaks = _find_pagebreaks(text)
    segments = _segment_ranges(text, breaks)

    # Group figures by segment index
    per_segment: dict[int, List[FigureSpec]] = {}
    for f in figures:
        if f.page_index is None or f.y is None or not f.page_height:
            continue
        seg_idx = int(f.page_index) - int(page_offset)
        if seg_idx < 0 or seg_idx >= len(segments):
            # Out of known range → append at end later
            continue
        per_segment.setdefault(seg_idx, []).append(f)

    # Build all insertions as (abs_position, html_snippet) and apply from end
    insertions: List[tuple[int, str]] = []
    for seg_idx, items in per_segment.items():
        start, end = segments[seg_idx]
        candidates = _find_block_end_positions(text, start, end)
        # Sort items by y ratio ascending to preserve reading order
        items_sorted = sorted(items, key=lambda it: (it.y or 0) / float(it.page_height or 1))
        for it in items_sorted:
            rel_src = os.path.relpath(it.image_path, html_path.parent).replace("\\", "/")
            ratio = (float(it.y) / float(it.page_height)) if it.page_height else 1.0
            pos = _choose_insertion_index(candidates, ratio, end)
            html = _build_marked_figure(rel_src, it.label, add_markers=remove_math_text)
            insertions.append((pos, html))

    # Fallback items with missing/invalid placement → append before </body>
    fallback_items = [f for f in figures if f not in sum(per_segment.values(), [])]
    if fallback_items:
        end_body = text.lower().rfind("</body>")
        tail_pos = end_body if end_body != -1 else len(text)
        for f in fallback_items:
            rel_src = os.path.relpath(f.image_path, html_path.parent).replace("\\", "/")
            insertions.append((tail_pos, _build_marked_figure(rel_src, f.label, add_markers=remove_math_text)))

    if not insertions:
        # Nothing to insert
        return

    # Apply insertions from end to start to keep indices valid
    insertions.sort(key=lambda t: t[0], reverse=True)
    new_text = text
    for pos, snippet in insertions:
        new_text = new_text[:pos] + ("\n" + snippet + "\n") + new_text[pos:]

    if remove_math_text:
        new_text = _remove_nearby_math_text(new_text)

    html_path.write_text(new_text, encoding="utf-8")

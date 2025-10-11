"""Predefined option sets and helpers to mix them safely."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, MutableMapping, Sequence, Tuple

__all__ = [
    "Preset",
    "PresetConflict",
    "PresetMergeReport",
    "apply_preset",
    "apply_presets",
    "get_presets",
]


@dataclass(frozen=True)
class Preset:
    """Bundle of options applied on top of the current configuration."""

    id: str
    name: str
    description: str
    layer: str
    options: Dict[str, object]


@dataclass(frozen=True)
class PresetConflict:
    """Describe a clash between presets for a unique option."""

    option_id: str
    overridden_preset: str
    overridden_value: object
    winning_preset: str
    winning_value: object


@dataclass(frozen=True)
class PresetMergeReport:
    """Result of applying a group of presets."""

    applied: Tuple[Preset, ...]
    conflicts: Tuple[PresetConflict, ...]
    notes: Tuple[str, ...]


_LAYER_ORDER = {
    "base": 1,
    "columns": 2,
    "content": 3,
    "toc": 4,
    "cleanup": 5,
    "debug": 6,
    "custom": 99,
}

_UNIQUE_KEYS = {
    "output-profile",
    "pdf-engine",
    "epub-version",
    "base-font-size",
    "minimum-line-height",
    "change-justification",
    "flow-size",
    "epub-max-image-size",
    "start-reading-at",
    "chapter-mark",
}

_XPATH_KEYS = {
    "chapter",
    "page-breaks-before",
    "level1-toc",
    "level2-toc",
    "level3-toc",
}

_CONCAT_KEYS = {"extra-css"}
_CSV_KEYS = {"filter-css"}


def _layer_sort_key(preset: Preset, position: int) -> Tuple[int, int]:
    layer_rank = _LAYER_ORDER.get(preset.layer, 98)
    return (layer_rank, position)


def _merge_xpath(existing: object, new_value: object) -> object:
    """Combine XPath expressions by OR-ing unique fragments."""

    def _split(expression: str) -> List[str]:
        parts: List[str] = []
        for raw in expression.split("|"):
            text = raw.strip()
            if text.startswith("(") and text.endswith(")"):
                text = text[1:-1].strip()
            if text:
                parts.append(text)
        return parts

    updated: List[str] = []
    for expr in _split(str(existing)):
        if expr not in updated:
            updated.append(expr)
    for expr in _split(str(new_value)):
        if expr not in updated:
            updated.append(expr)
    if not updated:
        return ""
    if len(updated) == 1:
        return updated[0]
    return " | ".join(updated)


def _merge_css(existing: object, new_value: object) -> str:
    segments = []
    for raw in str(existing).split(","):
        entry = raw.strip()
        if entry and entry not in segments:
            segments.append(entry)
    for raw in str(new_value).split(","):
        entry = raw.strip()
        if entry and entry not in segments:
            segments.append(entry)
    return ",".join(segments)


def _merge_text_block(existing: object, new_value: object) -> str:
    base = str(existing).rstrip()
    addition = str(new_value).strip()
    if not addition:
        return base
    if not base:
        return addition
    return f"{base}\n{addition}"


_PRESETS: List[Preset] = [
    # -- Base layer --------------------------------------------------------
    Preset(
        id="base-kobo",
        name="Kobo (Clara/Libra)",
        description="Perfil Kobo con EPUB 3 y tamaño de fuente equilibrado.",
        layer="base",
        options={
            "output-profile": "kobo",
            "epub-version": "3",
            "base-font-size": "12",
            "minimum-line-height": "1.3",
            "font-size-mapping": "8,9,10,11,12,13,14,16",
            "pdf-engine": "calibre",
            "pdf-header-skip": "-1",
            "pdf-footer-skip": "-1",
        },
    ),
    # -- Columns layer -----------------------------------------------------
    Preset(
        id="col-1",
        name="PDF 1 columna",
        description="Unwrap suave manteniendo párrafos largos juntos.",
        layer="columns",
        options={
            "unwrap-factor": "0.35",
        },
    ),
    Preset(
        id="col-2",
        name="PDF 2 columnas",
        description="Unwrap más agresivo y división de flujo moderada.",
        layer="columns",
        options={
            "unwrap-factor": "0.20",
            "flow-size": "650",
        },
    ),
    Preset(
        id="col-fijo",
        name="Respetar saltos originales",
        description="Conserva el maquetado original, sin unwrap automático.",
        layer="columns",
        options={
            "disable-unwrap-lines": True,
        },
    ),
    # -- Content layer -----------------------------------------------------
    Preset(
        id="combo-tech",
        name="Técnico con fórmulas",
        description="Activa heurísticas suaves, incrusta fuentes y evita cortes abruptos.",
        layer="content",
        options={
            "enable-heuristics": True,
            "dont-split-on-page-breaks": True,
            "keep-ligatures": True,
            "embed-all-fonts": True,
            "subset-embedded-fonts": True,
        },
    ),
    Preset(
        id="math-conservador",
        name="Matemáticas conservador",
        description="Mantiene saltos de línea originales para preservar fórmulas.",
        layer="content",
        options={
            "enable-heuristics": True,
            "disable-unwrap-lines": True,
            "keep-ligatures": True,
            "subset-embedded-fonts": True,
        },
    ),
    Preset(
        id="img-fidelidad",
        name="Imágenes a tamaño perfil",
        description="Ajusta imágenes al perfil y añade CSS para evitar cortes.",
        layer="content",
        options={
            "epub-max-image-size": "profile",
            "preserve-cover-aspect-ratio": True,
            "extra-css": "img,figure{break-inside:avoid;page-break-inside:avoid;}\nimg{max-width:100%;height:auto;}",
        },
    ),
    Preset(
        id="img-ligero",
        name="Imágenes ligeras",
        description="Reduce el tamaño y filtra propiedades pesadas.",
        layer="content",
        options={
            "epub-max-image-size": "1200x1600",
            "filter-css": "box-shadow,filter",
        },
    ),
    # -- TOC layer ---------------------------------------------------------
    Preset(
        id="toc-robusto-es",
        name="TOC robusto (ES)",
        description="Detecta encabezados h1/h2, añade salto y TOC exhaustivo.",
        layer="toc",
        options={
            "chapter": "//h:h1 | //h:h2",
            "chapter-mark": "pagebreak",
            "page-breaks-before": "//h:figure | //h:h1 | //h:h2",
            "level1-toc": "//h:h1 | //h:h2",
            "level2-toc": "//h:h3",
            "toc-threshold": "0",
            "max-toc-links": "0",
        },
    ),
    Preset(
        id="toc-minimo",
        name="TOC mínimo",
        description="Solo títulos h1 para un índice compacto.",
        layer="toc",
        options={
            "chapter": "//h:h1",
            "chapter-mark": "pagebreak",
            "level1-toc": "//h:h1",
            "toc-threshold": "5",
        },
    ),
    Preset(
        id="toc-sencillo",
        name="Sin índice automático",
        description="No genera TOC automático ni marca capítulos.",
        layer="toc",
        options={
            "chapter-mark": "none",
            "no-chapters-in-toc": True,
        },
    ),
    # -- Cleanup layer -----------------------------------------------------
    Preset(
        id="cleanup-suave",
        name="Limpieza suave",
        description="Quita espacios dobles y respeta sangrías manuales.",
        layer="cleanup",
        options={
            "enable-heuristics": True,
            "disable-delete-blank-paragraphs": True,
            "disable-fix-indents": True,
        },
    ),
    Preset(
        id="cleanup-css",
        name="Limpieza CSS",
        description="Filtra márgenes laterales y añade CSS de normalización.",
        layer="cleanup",
        options={
            "filter-css": "margin-left,margin-right,padding-left,padding-right",
            "extra-css": "body{margin:0 auto;}",
        },
    ),
    Preset(
        id="cleanup-parrafos",
        name="Párrafos compactos",
        description="Elimina espacios entre párrafos y aplica sangría ligera.",
        layer="cleanup",
        options={
            "remove-paragraph-spacing": True,
            "remove-paragraph-spacing-indent-size": "1.2",
        },
    ),
    # -- Debug layer -------------------------------------------------------
    Preset(
        id="debug-verbose",
        name="Verbose",
        description="Aumenta la verbosidad del proceso de conversión.",
        layer="debug",
        options={
            "verbose": True,
        },
    ),
    Preset(
        id="debug-pipeline",
        name="Guardar pipeline",
        description="Vuelca los artefactos intermedios en './debug-oeb'.",
        layer="debug",
        options={
            "debug-pipeline": "./debug-oeb",
        },
    ),
]

_PRESET_INDEX = {preset.id: index for index, preset in enumerate(_PRESETS)}


def get_presets() -> List[Preset]:
    """Return the list of available presets, ordered by logical layer."""

    return list(_PRESETS)


def apply_presets(options: MutableMapping[str, object], presets: Sequence[Preset]) -> PresetMergeReport:
    """Merge several presets into the provided options mapping."""

    for legacy_key in ("line-height",):
        options.pop(legacy_key, None)

    if not presets:
        return PresetMergeReport(applied=tuple(), conflicts=tuple(), notes=tuple())

    ordered = tuple(
        preset for _, preset in sorted(
            enumerate(presets), key=lambda item: _layer_sort_key(item[1], item[0])
        )
    )

    conflicts: List[PresetConflict] = []
    notes: List[str] = []
    sources: Dict[str, Tuple[Preset, object]] = {}

    for preset in ordered:
        for option_id, raw_value in preset.options.items():
            if option_id in _XPATH_KEYS and option_id in options:
                options[option_id] = _merge_xpath(options[option_id], raw_value)
                continue

            if option_id in _CONCAT_KEYS and option_id in options:
                options[option_id] = _merge_text_block(options[option_id], raw_value)
                continue

            if option_id in _CSV_KEYS and option_id in options:
                options[option_id] = _merge_css(options[option_id], raw_value)
                continue

            if option_id in _XPATH_KEYS:
                options[option_id] = raw_value
                continue

            if option_id in _CONCAT_KEYS:
                options[option_id] = str(raw_value).strip()
                continue

            if option_id in _CSV_KEYS:
                options[option_id] = _merge_css("", raw_value)
                continue

            if option_id in _UNIQUE_KEYS:
                previous = sources.get(option_id)
                current_value = options.get(option_id)
                incoming_value = raw_value
                if previous and previous[1] != incoming_value:
                    conflicts.append(
                        PresetConflict(
                            option_id=option_id,
                            overridden_preset=previous[0].name,
                            overridden_value=previous[1],
                            winning_preset=preset.name,
                            winning_value=incoming_value,
                        )
                    )
                sources[option_id] = (preset, incoming_value)
                options[option_id] = incoming_value
                continue

            options[option_id] = raw_value
            if option_id not in sources:
                sources[option_id] = (preset, raw_value)
            else:
                sources[option_id] = (preset, raw_value)

    if not options.get("enable-heuristics"):
        removed = [key for key in list(options) if key.startswith("disable-")]
        for key in removed:
            options.pop(key, None)
        if removed:
            removed.sort()
            joined = ", ".join(removed)
            notes.append(f"Se ignoraron {joined} porque enable-heuristics no está activo.")

    if options.get("disable-unwrap-lines"):
        removed_keys = []
        for target in ("unwrap-factor", "html-unwrap-factor"):
            if target in options:
                options.pop(target, None)
                removed_keys.append(target)
        if removed_keys:
            joined = ", ".join(sorted(removed_keys))
            notes.append(f"disable-unwrap-lines elimina {joined} de la mezcla.")

    return PresetMergeReport(
        applied=ordered,
        conflicts=tuple(conflicts),
        notes=tuple(notes),
    )


def apply_preset(options: MutableMapping[str, object], preset: Preset) -> PresetMergeReport:
    """Compatibility wrapper to apply a single preset."""

    return apply_presets(options, (preset,))

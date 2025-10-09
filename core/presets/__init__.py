"""Predefined option sets tailored for common conversion scenarios."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List

__all__ = [
    "Preset",
    "apply_preset",
    "get_presets",
]


@dataclass(frozen=True)
class Preset:
    """Bundle of options applied on top of the current configuration."""

    id: str
    name: str
    description: str
    category: str
    options: Dict[str, object]


_PRESETS: List[Preset] = [
    Preset(
        id="device-kindle",
        name="Kindle Paperwhite",
        description="Optimiza fuentes y margen para Kindle Paperwhite.",
        category="device",
        options={
            "output-profile": "kindle_pw",
            "base-font-size": "12",
            "minimum-line-height": "1.30",
        },
    ),
    Preset(
        id="device-kobo",
        name="Kobo Clara/Libra",
        description="Perfil de salida Kobo con tamaño de fuente moderado.",
        category="device",
        options={
            "output-profile": "kobo",
            "base-font-size": "12",
            "minimum-line-height": "1.25",
        },
    ),
    Preset(
        id="device-tablet",
        name="Tablet / iPad",
        description="Aumenta el tamaño de fuente para pantallas grandes.",
        category="device",
        options={
            "output-profile": "tablet",
            "base-font-size": "14",
            "minimum-line-height": "1.20",
        },
    ),
    Preset(
        id="pdf-technical",
        name="PDF técnico 1 columna",
        description="Activa heurísticas suaves y mantiene márgenes." ,
        category="pdf",
        options={
            "enable-heuristics": True,
            "dont-split-on-page-breaks": True,
            "minimum-line-height": "1.35",
            "disable-remove-fake-margins": True,
        },
    ),
    Preset(
        id="pdf-formulas",
        name="PDF con fórmulas",
        description="Amplía fuente base y evita modificaciones agresivas.",
        category="pdf",
        options={
            "base-font-size": "15",
            "minimum-line-height": "1.30",
            "disable-heuristics": True,
            "keep-ligatures": True,
        },
    ),
]


def get_presets() -> List[Preset]:
    """Return the list of available presets."""

    return list(_PRESETS)


def apply_preset(options: Dict[str, object], preset: Preset) -> None:
    """Merge the preset options into the provided mapping."""

    # Limpia flags heredadas que ya no son compatibles con los presets actuales.
    for legacy_key in ("line-height",):
        options.pop(legacy_key, None)

    options.update(preset.options)

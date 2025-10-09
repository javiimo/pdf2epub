"""Serialization helpers for GUI tab configurations.

This module defines the data structure that represents a single GUI
configuration tab and provides helpers to persist and restore it from JSON
documents. The goal is to keep the representation independent from the Tk
layer so tests and higher-level logic can operate on plain Python objects.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

__all__ = [
    "ConfigurationError",
    "TabConfiguration",
    "load_configuration",
    "save_configuration",
]


class ConfigurationError(RuntimeError):
    """Raised when a configuration payload is invalid or cannot be persisted."""


_RESERVED_KEYS = {
    "tab_id",
    "title",
    "input_pdf",
    "output_epub",
    "page_range",
    "options",
    "notes",
}


def _coerce_optional_path(value: Any, field_name: str) -> Optional[Path]:
    if value is None:
        return None

    if isinstance(value, Path):
        return value

    if isinstance(value, os.PathLike):
        return Path(value)

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        return Path(stripped)

    raise ConfigurationError(f"'{field_name}' debe ser una ruta o None.")


def _coerce_non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ConfigurationError(f"'{field_name}' debe ser una cadena.")
    stripped = value.strip()
    if not stripped:
        raise ConfigurationError(f"'{field_name}' no puede estar vacío.")
    return stripped


def _coerce_optional_string(value: Any, field_name: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigurationError(f"'{field_name}' debe ser una cadena o None.")
    stripped = value.strip()
    return stripped or None


def _coerce_options(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigurationError("'options' debe ser un objeto JSON (dict).")

    normalised: Dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ConfigurationError("Las claves de 'options' deben ser cadenas.")
        normalised[key] = item
    return normalised


def _coerce_extras(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigurationError("'extras' debe ser un objeto JSON (dict).")

    extras: Dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ConfigurationError("Las claves de 'extras' deben ser cadenas.")
        if key in _RESERVED_KEYS:
            raise ConfigurationError(
                f"La clave reservada '{key}' no puede aparecer en 'extras'."
            )
        extras[key] = item
    return extras


def _path_to_string(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    return str(path)


@dataclass
class TabConfiguration:
    """Serializable representation of a configuration tab."""

    tab_id: str
    title: str
    input_pdf: Optional[Path] = None
    output_epub: Optional[Path] = None
    page_range: Optional[str] = None
    options: Dict[str, Any] = field(default_factory=dict)
    notes: Optional[str] = None
    extras: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.tab_id = _coerce_non_empty_string(self.tab_id, "tab_id")
        self.title = _coerce_non_empty_string(self.title, "title")
        self.input_pdf = _coerce_optional_path(self.input_pdf, "input_pdf")
        self.output_epub = _coerce_optional_path(self.output_epub, "output_epub")
        self.page_range = _coerce_optional_string(self.page_range, "page_range")
        # For notes we allow empty strings to survive (useful for UI placeholders).
        if self.notes is not None and not isinstance(self.notes, str):
            raise ConfigurationError("'notes' debe ser una cadena o None.")
        self.options = _coerce_options(self.options)
        self.extras = _coerce_extras(self.extras)

    def to_dict(self) -> Dict[str, Any]:
        """Render the configuration as a JSON-serialisable dictionary."""
        payload: Dict[str, Any] = {
            "tab_id": self.tab_id,
            "title": self.title,
            "input_pdf": _path_to_string(self.input_pdf),
            "output_epub": _path_to_string(self.output_epub),
            "page_range": self.page_range,
            "options": self.options,
            "notes": self.notes,
        }
        if self.extras:
            payload.update(self.extras)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "TabConfiguration":
        """Build an instance from a raw dictionary."""
        if not isinstance(data, Mapping):
            raise ConfigurationError("El contenido del JSON debe ser un objeto.")

        extras = {
            key: value for key, value in data.items() if key not in _RESERVED_KEYS
        }

        return cls(
            tab_id=data.get("tab_id"),
            title=data.get("title"),
            input_pdf=data.get("input_pdf"),
            output_epub=data.get("output_epub"),
            page_range=data.get("page_range"),
            options=data.get("options"),
            notes=data.get("notes"),
            extras=extras,
        )


def load_configuration(path: Path) -> TabConfiguration:
    """Load a configuration from a JSON file."""
    resolved = Path(path)
    if not resolved.exists():
        raise ConfigurationError(f"No se encontró el archivo de configuración: {resolved}")

    try:
        with resolved.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(
            f"El JSON de '{resolved}' es inválido: {exc}"
        ) from exc
    except OSError as exc:
        raise ConfigurationError(
            f"No se pudo leer el archivo de configuración '{resolved}': {exc}"
        ) from exc

    return TabConfiguration.from_dict(raw)


def save_configuration(config: TabConfiguration, path: Path) -> None:
    """Persist a configuration to disk in JSON format."""
    resolved = Path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)

    payload = config.to_dict()

    try:
        with resolved.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
    except OSError as exc:
        raise ConfigurationError(
            f"No se pudo escribir el archivo de configuración '{resolved}': {exc}"
        ) from exc

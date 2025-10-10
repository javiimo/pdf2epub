"""Lightweight persistence for user interface preferences."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

__all__ = ["SettingsError", "UiSettings", "load_settings", "save_settings", "settings_path"]


class SettingsError(RuntimeError):
    """Raised when UI settings cannot be loaded or saved."""


@dataclass
class UiSettings:
    """Serializable container for UI preferences."""

    font_size: int = 11

    def clamp(self, *, minimum: int = 8, maximum: int = 24) -> None:
        """Ensure settings values stay within sensible bounds."""
        if self.font_size < minimum:
            self.font_size = minimum
        elif self.font_size > maximum:
            self.font_size = maximum

    def to_dict(self) -> Dict[str, Any]:
        return {"font_size": int(self.font_size)}

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "UiSettings":
        size = payload.get("font_size")
        if not isinstance(size, int):
            raise SettingsError("El tamaño de fuente almacenado es inválido.")
        instance = cls(font_size=size)
        instance.clamp()
        return instance


def _platform_config_root() -> Path:
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        if base:
            return Path(base)
        return Path.home() / "AppData" / "Roaming"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def settings_path() -> Path:
    """Return the canonical path to the settings JSON file."""

    return _platform_config_root() / "pdf2epub" / "settings.json"


def load_settings() -> UiSettings:
    """Load UI settings from disk, falling back to defaults on error."""

    path = settings_path()
    if not path.exists():
        return UiSettings()

    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as exc:
        raise SettingsError(f"El archivo de preferencias está corrupto ({path}): {exc}") from exc
    except OSError as exc:
        raise SettingsError(f"No se pudieron leer las preferencias ({path}): {exc}") from exc

    if not isinstance(raw, dict):
        raise SettingsError("El archivo de preferencias tiene un formato inesperado.")

    return UiSettings.from_dict(raw)


def save_settings(settings: UiSettings) -> None:
    """Persist UI settings to disk."""

    path = settings_path()
    settings.clamp()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(settings.to_dict(), handle, ensure_ascii=False, indent=2)
    except OSError as exc:
        raise SettingsError(f"No se pudieron guardar las preferencias ({path}): {exc}") from exc

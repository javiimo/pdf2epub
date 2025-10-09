"""Loader and helpers for the ebook-convert options catalogue.

This module centralises access to the structured metadata defined in
``assets/options_catalog.json`` so the GUI layer can render forms and validate
inputs consistently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

# The catalogue is stored alongside other static assets at the project root.
DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[2] / "assets" / "options_catalog.json"


class CatalogError(RuntimeError):
    """Raised when the options catalogue cannot be parsed or is inconsistent."""


@dataclass(frozen=True)
class CategoryMetadata:
    """Group metadata for a logical subset of options."""

    id: str
    label: str
    description: str


@dataclass(frozen=True)
class OptionMetadata:
    """Metadata describing a single ebook-convert CLI flag."""

    id: str
    cli: str
    value_type: str
    domain: Optional[Mapping[str, object]]
    description: str
    category: str
    depends_on: Tuple[str, ...]
    aliases: Tuple[str, ...] = ()
    notes: Optional[str] = None
    repeatable: bool = False
    hidden: bool = False

    def all_cli_names(self) -> Tuple[str, ...]:
        """Return the primary CLI flag plus any aliases."""
        if not self.aliases:
            return (self.cli,)
        return (self.cli, *self.aliases)


@dataclass(frozen=True)
class Catalog:
    """Structured representation of the options catalogue."""

    version: int
    categories: Mapping[str, CategoryMetadata]
    options: Mapping[str, OptionMetadata]

    def option_by_id(self, option_id: str) -> Optional[OptionMetadata]:
        """Look up an option by its unique identifier."""
        return self.options.get(option_id)

    def option_by_cli(self, cli_flag: str) -> Optional[OptionMetadata]:
        """Look up an option by CLI flag or alias (case sensitive)."""
        for option in self.options.values():
            if cli_flag == option.cli or cli_flag in option.aliases:
                return option
        return None

    def options_for_category(self, category_id: str) -> Iterable[OptionMetadata]:
        """Yield options that belong to a given category identifier."""
        for option in self.options.values():
            if option.category == category_id and not option.hidden:
                yield option


def _build_categories(entries: Sequence[Mapping[str, object]]) -> Dict[str, CategoryMetadata]:
    categories: Dict[str, CategoryMetadata] = {}
    for entry in entries:
        try:
            category = CategoryMetadata(
                id=str(entry["id"]),
                label=str(entry["label"]),
                description=str(entry.get("description", "")),
            )
        except KeyError as exc:
            raise CatalogError(f"Categoría incompleta en catálogo: falta {exc}") from exc

        if category.id in categories:
            raise CatalogError(f"Categoría duplicada en catálogo: {category.id}")

        categories[category.id] = category

    return categories


def _build_option(entry: Mapping[str, object], categories: Mapping[str, CategoryMetadata]) -> OptionMetadata:
    try:
        option_id = str(entry["id"])
        cli = str(entry["cli"])
        value_type = str(entry["value_type"])
        description = str(entry["description"])
        category_id = str(entry["category"])
    except KeyError as exc:
        raise CatalogError(f"Opción incompleta en catálogo: falta {exc}") from exc

    if category_id not in categories:
        raise CatalogError(f"La opción '{option_id}' referencia categoría desconocida '{category_id}'")

    domain = entry.get("domain")
    if domain is not None and not isinstance(domain, Mapping):
        raise CatalogError(f"La opción '{option_id}' define un dominio inválido (se esperaba objeto).")

    depends_raw = entry.get("depends_on", [])
    aliases_raw = entry.get("aliases", [])

    if not isinstance(depends_raw, Sequence) or isinstance(depends_raw, (str, bytes)):
        raise CatalogError(f"La opción '{option_id}' tiene depends_on inválido (se esperaba lista).")
    if not isinstance(aliases_raw, Sequence) or isinstance(aliases_raw, (str, bytes)):
        raise CatalogError(f"La opción '{option_id}' tiene aliases inválidas (se esperaba lista).")

    depends_on = tuple(str(flag) for flag in depends_raw)
    aliases = tuple(str(alias) for alias in aliases_raw)

    repeatable = bool(entry.get("repeatable", False))
    hidden = bool(entry.get("hidden", False))
    notes = entry.get("notes")
    if notes is not None:
        notes = str(notes)

    return OptionMetadata(
        id=option_id,
        cli=cli,
        value_type=value_type,
        domain=domain,  # type: ignore[arg-type]
        description=description,
        category=category_id,
        depends_on=depends_on,
        aliases=aliases,
        notes=notes,
        repeatable=repeatable,
        hidden=hidden,
    )


def _build_options(entries: Sequence[Mapping[str, object]], categories: Mapping[str, CategoryMetadata]) -> Dict[str, OptionMetadata]:
    options: Dict[str, OptionMetadata] = {}
    for entry in entries:
        option = _build_option(entry, categories)
        if option.id in options:
            raise CatalogError(f"Opción duplicada en catálogo: {option.id}")
        options[option.id] = option
    return options


def load_catalog(path: Optional[Path] = None) -> Catalog:
    """Load the options catalogue from disk."""
    resolved_path = Path(path) if path is not None else DEFAULT_CATALOG_PATH

    if not resolved_path.exists():
        raise CatalogError(f"No se encontró el catálogo de opciones en {resolved_path}")

    try:
        with resolved_path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
    except OSError as exc:
        raise CatalogError(f"Error al abrir el catálogo '{resolved_path}': {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"Archivo de catálogo inválido '{resolved_path}': {exc}") from exc

    try:
        version = int(raw["version"])
        category_entries = raw.get("categories", [])
        option_entries = raw.get("options", [])
    except KeyError as exc:
        raise CatalogError(f"Catálogo sin campo requerido: {exc}") from exc

    if (
        not isinstance(category_entries, Sequence)
        or isinstance(category_entries, (str, bytes))
        or not isinstance(option_entries, Sequence)
        or isinstance(option_entries, (str, bytes))
    ):
        raise CatalogError("Los campos 'categories' y 'options' deben ser listas.")

    categories = _build_categories(category_entries)  # type: ignore[arg-type]
    options = _build_options(option_entries, categories)  # type: ignore[arg-type]

    return Catalog(version=version, categories=categories, options=options)


@lru_cache(maxsize=4)
def get_catalog(path: Optional[Path] = None) -> Catalog:
    """Load and cache the catalogue. The path is part of the cache key."""
    return load_catalog(path)

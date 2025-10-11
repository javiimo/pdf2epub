"""Loader and helpers for the ebook-convert options catalogue.

This module centralises access to the structured metadata defined in
``assets/options_catalog.json`` so the GUI layer can render forms and validate
inputs consistently.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

if TYPE_CHECKING:  # pragma: no cover - typing only
    from core.runner.cli_support import CliSupportInfo

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


def hide_unsupported_options(
    catalog: Catalog,
    supported_flags: Optional[Iterable[str]],
) -> Tuple[Catalog, Tuple[OptionMetadata, ...]]:
    """Return a catalog copy where unsupported options are hidden.

    Parameters
    ----------
    catalog:
        Source catalogue to inspect.
    supported_flags:
        Iterable with the CLI flags recognised by the current ebook-convert
        binary. When ``None`` or empty, the catalogue is returned unchanged.

    Returns
    -------
    Tuple[Catalog, Tuple[OptionMetadata, ...]]
        A tuple with the filtered catalogue and an ordered tuple with the
        options that were hidden.
    """
    if not supported_flags:
        return catalog, ()

    supported: Set[str] = {str(flag) for flag in supported_flags}
    unsupported_ids: Set[str] = {
        option.id
        for option in catalog.options.values()
        if not option.hidden and not any(flag in supported for flag in option.all_cli_names())
    }

    if not unsupported_ids:
        return catalog, ()

    changed = True
    while changed:
        changed = False
        for option in catalog.options.values():
            if option.hidden or option.id in unsupported_ids:
                continue
            for dependency_flag in option.depends_on:
                dependency = catalog.option_by_cli(dependency_flag)
                if dependency is None:
                    continue
                if dependency.id in unsupported_ids:
                    unsupported_ids.add(option.id)
                    changed = True
                    break

    if not unsupported_ids:
        return catalog, ()

    new_options: Dict[str, OptionMetadata] = dict(catalog.options)
    hidden: List[OptionMetadata] = []
    for option_id in unsupported_ids:
        original = catalog.options.get(option_id)
        if original is None:
            continue
        new_options[option_id] = replace(original, hidden=True)
        hidden.append(original)

    if not hidden:
        return catalog, ()

    hidden.sort(key=lambda opt: opt.cli)
    filtered = Catalog(
        version=catalog.version,
        categories=catalog.categories,
        options=new_options,
    )
    return filtered, tuple(hidden)


_UNKNOWN_CATEGORY = CategoryMetadata(
    id="detected",
    label="Flags detectadas",
    description="Opciones adicionales encontradas en tu versión de ebook-convert. Úsalas con precaución.",
)


_TYPE_HINTS = {
    "int": "integer",
    "integer": "integer",
    "float": "float",
    "double": "float",
    "number": "float",
    "path": "path",
    "file": "path",
    "dir": "path",
    "directory": "path",
    "bool": "boolean",
    "boolean": "boolean",
}


def _sanitize_option_id(flag: str) -> str:
    base = flag.lstrip("-").replace("-", "_")
    if not base:
        base = "flag"
    return f"detected-{base}"


def _infer_value_type(flag: str, help_text: str) -> str:
    # Consider first line only for heuristics.
    first_line = help_text.splitlines()[0] if help_text else flag
    lower_line = first_line.lower()
    after_flag = first_line.split(flag, 1)[-1]
    token = after_flag.strip()
    if not token:
        return "boolean"
    if token.startswith(",") or token.startswith("--"):
        return "boolean"
    if token.startswith("=") or token.startswith("[") or token.startswith("<"):
        # Try to detect explicit type hints inside the token.
        stripped = token.lstrip("=[<").rstrip(">]")
        for hint, type_name in _TYPE_HINTS.items():
            if hint in stripped:
                return type_name
        return "string"
    # Fallback: look for explicit type keywords in the whole line.
    for hint, type_name in _TYPE_HINTS.items():
        if f"<{hint}>" in lower_line or hint in token.lower():
            return type_name
    # Default to string accepting manual input.
    return "string"


def augment_with_detected_options(
    catalog: Catalog,
    info: Optional["CliSupportInfo"],
) -> Tuple[Catalog, Tuple[OptionMetadata, ...]]:
    """Extend the catalogue with flags detected in the current binary."""

    if info is None or not info.flags:
        return catalog, ()

    known_cli: Set[str] = set()
    for option in catalog.options.values():
        known_cli.update(option.all_cli_names())

    detected: List[OptionMetadata] = []
    new_options: Dict[str, OptionMetadata] = {}
    for flag in sorted(info.flags):
        if flag in known_cli:
            continue
        option_id = _sanitize_option_id(flag)
        if option_id in catalog.options or option_id in new_options:
            continue
        help_text = info.help_by_flag.get(flag, flag)
        value_type = _infer_value_type(flag, help_text)
        description = (
            "Detectada automáticamente a partir de la ayuda de ebook-convert.\n\n"
            f"{help_text}"
        )
        option = OptionMetadata(
            id=option_id,
            cli=flag,
            value_type=value_type,
            domain=None,
            description=description,
            category=_UNKNOWN_CATEGORY.id,
            depends_on=(),
            aliases=(),
            notes="Añadida automáticamente; revisa la ayuda oficial para confirmar parámetros.",
            repeatable=False,
            hidden=False,
        )
        detected.append(option)
        new_options[option_id] = option

    if not detected:
        return catalog, ()

    categories = dict(catalog.categories)
    categories.setdefault(_UNKNOWN_CATEGORY.id, _UNKNOWN_CATEGORY)

    options = dict(catalog.options)
    options.update(new_options)

    return (
        Catalog(version=catalog.version, categories=categories, options=options),
        tuple(detected),
    )


@lru_cache(maxsize=4)
def get_catalog(path: Optional[Path] = None) -> Catalog:
    """Load and cache the catalogue. The path is part of the cache key."""
    return load_catalog(path)

"""Validation helpers for ebook-convert option values."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional
from xml.etree import ElementTree

from core.configuration import TabConfiguration
from core.options.catalog import Catalog, OptionMetadata

try:  # pragma: no cover - optional dependency
    from lxml import etree
except ImportError:  # pragma: no cover - fallback path
    etree = None


def _domain_values(option: OptionMetadata) -> Optional[Iterable[str]]:
    if not option.domain:
        return None
    values = option.domain.get("values")
    if isinstance(values, list):
        return [str(value) for value in values]
    return None


def _domain_range(option: OptionMetadata) -> tuple[Optional[float], Optional[float]]:
    if not option.domain:
        return None, None
    if option.domain.get("kind") == "range":
        return option.domain.get("min"), option.domain.get("max")
    return None, None


def _infer_format(option: OptionMetadata) -> Optional[str]:
    if not option.domain:
        return None
    return option.domain.get("format")


def _is_truthy(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "off")
    return True


class OptionValidator:
    """Validate option values using metadata from the catalogue."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    def validate(self, option: OptionMetadata, config: TabConfiguration) -> List[str]:
        value = config.options.get(option.id)
        if value is None or value == "":
            return []

        errors: List[str] = []

        if option.value_type == "integer":
            errors.extend(self._validate_integer(option, value))
        elif option.value_type == "float":
            errors.extend(self._validate_float(option, value))
        elif option.value_type == "enum":
            errors.extend(self._validate_enum(option, value))
        elif option.value_type == "path":
            errors.extend(self._validate_path(option, value))
        elif option.value_type == "string":
            errors.extend(self._validate_format(option, value))

        if option.value_type == "boolean" and option.repeatable:
            try:
                int(value)
            except (TypeError, ValueError):
                errors.append("Debe ser un número entero (veces que se repite la bandera).")

        return errors

    def missing_dependencies(self, option: OptionMetadata, config: TabConfiguration) -> List[str]:
        missing: List[str] = []
        for cli_flag in option.depends_on:
            dependency = self.catalog.option_by_cli(cli_flag)
            if dependency is None:
                continue
            if not _is_truthy(config.options.get(dependency.id)):
                missing.append(dependency.cli)
        return missing

    # -- Type specific validations ------------------------------------

    def _validate_integer(self, option: OptionMetadata, value: object) -> List[str]:
        try:
            numeric = int(value)
        except (TypeError, ValueError):
            return ["Debe ser un número entero."]

        minimum, maximum = _domain_range(option)
        errors: List[str] = []
        if minimum is not None and numeric < minimum:
            errors.append(f"Debe ser mayor o igual que {int(minimum)}.")
        if maximum is not None and numeric > maximum:
            errors.append(f"Debe ser menor o igual que {int(maximum)}.")
        return errors

    def _validate_float(self, option: OptionMetadata, value: object) -> List[str]:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return ["Debe ser un número (puede incluir decimales)."]

        minimum, maximum = _domain_range(option)
        errors: List[str] = []
        if minimum is not None and numeric < float(minimum):
            errors.append(f"Debe ser mayor o igual que {minimum}.")
        if maximum is not None and numeric > float(maximum):
            errors.append(f"Debe ser menor o igual que {maximum}.")
        return errors

    def _validate_enum(self, option: OptionMetadata, value: object) -> List[str]:
        values = _domain_values(option)
        if not values:
            return []
        if str(value) not in values:
            return [f"Valor no válido. Opciones permitidas: {', '.join(values)}."]
        return []

    def _validate_path(self, option: OptionMetadata, value: object) -> List[str]:
        if not isinstance(value, str):
            return ["La ruta debe ser una cadena."]

        path = Path(value).expanduser()
        path_type = (option.domain or {}).get("path_type")
        if path_type == "file":
            if not path.exists() or not path.is_file():
                return ["Selecciona un archivo existente."]
        elif path_type == "directory":
            if not path.exists() or not path.is_dir():
                return ["Selecciona un directorio existente."]
        else:
            if not path.exists():
                return ["Selecciona una ruta válida."]
        return []

    def _validate_format(self, option: OptionMetadata, value: object) -> List[str]:
        fmt = _infer_format(option)
        if fmt == "regex":
            return self._validate_regex(value)
        if fmt == "xpath":
            return self._validate_xpath(value)
        return []

    def _validate_regex(self, value: object) -> List[str]:
        if not isinstance(value, str):
            return ["La expresión debe ser una cadena."]
        try:
            re.compile(value)
        except re.error as exc:
            return [f"Expresión regular inválida: {exc}."]
        return []

    def _validate_xpath(self, value: object) -> List[str]:
        if not isinstance(value, str):
            return ["El XPath debe ser una cadena."]
        expression = value.strip()
        if not expression:
            return ["El XPath no puede estar vacío."]

        if etree is not None:  # pragma: no cover - requires optional dependency
            try:
                etree.XPath(expression)
            except etree.XPathSyntaxError as exc:
                return [f"XPath inválido: {exc}."]
            return []

        # Fallback using stdlib ElementTree heuristics.
        root = ElementTree.Element("root")
        try:
            ElementTree.ElementTree(root).findall(expression)
        except (SyntaxError, TypeError, ValueError) as exc:
            return [f"XPath inválido: {exc}."]
        return []

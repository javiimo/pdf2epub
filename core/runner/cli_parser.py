"""Utilities to transform ebook-convert CLI strings into structured configs."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from core.configuration import TabConfiguration
from core.options.catalog import Catalog, OptionMetadata

__all__ = ["CliParseError", "CliParseResult", "parse_cli_command", "tab_configuration_from_cli"]


class CliParseError(RuntimeError):
    """Raised when a CLI string cannot be parsed into a configuration."""


@dataclass(frozen=True)
class CliParseResult:
    """Structured representation of an ebook-convert CLI invocation."""

    command: str
    input_path: Path
    output_path: Path
    options: Dict[str, object]


def _normalise_boolean(value: Optional[str]) -> bool:
    if value is None:
        return True
    lowered = value.strip().lower()
    return lowered not in {"0", "false", "no", "off"}


def _assign_option(result: Dict[str, object], option: OptionMetadata, value: object) -> None:
    key = option.id
    if option.value_type == "boolean" and option.repeatable:
        current = result.get(key, 0)
        if not isinstance(current, int):
            current = 0
        if bool(value):
            result[key] = int(current) + 1
        else:
            result[key] = int(current)
    elif option.repeatable:
        current = result.get(key)
        if current is None:
            result[key] = [value]
        elif isinstance(current, list):
            current.append(value)
        else:
            result[key] = [current, value]
    else:
        result[key] = value


def _resolve_option(flag: str, catalog: Catalog) -> OptionMetadata:
    option = catalog.option_by_cli(flag)
    if option is None:
        raise CliParseError(f"Opción CLI desconocida: {flag}")
    return option


def parse_cli_command(cli_line: str, catalog: Catalog) -> CliParseResult:
    """Parse a full ebook-convert command line."""
    try:
        parts = shlex.split(cli_line, comments=False, posix=True)
    except ValueError as exc:
        raise CliParseError(f"No se pudo tokenizar la línea CLI: {exc}") from exc

    if len(parts) < 3:
        raise CliParseError("La línea CLI debe contener al menos comando, entrada y salida.")

    command, *rest = parts
    if "ebook-convert" not in command:
        raise CliParseError("Solo se admiten comandos ebook-convert.")

    input_token = rest.pop(0)
    output_token = rest.pop(0)

    input_path = Path(input_token)
    output_path = Path(output_token)

    options: Dict[str, object] = {}
    index = 0

    while index < len(rest):
        raw = rest[index]
        if not raw.startswith("-"):
            raise CliParseError(f"Token inesperado sin prefijo '-': {raw}")

        inline_value: Optional[str] = None
        if "=" in raw and not raw.startswith("="):
            flag, inline_value = raw.split("=", 1)
        else:
            flag = raw

        option = _resolve_option(flag, catalog)

        if option.value_type == "boolean":
            value = _normalise_boolean(inline_value)
            index += 1
        else:
            if inline_value is not None:
                value = inline_value
                index += 1
            else:
                if index + 1 >= len(rest):
                    raise CliParseError(f"La opción {flag} requiere un valor.")
                value = rest[index + 1]
                index += 2

        _assign_option(options, option, value)

    return CliParseResult(
        command=command,
        input_path=input_path,
        output_path=output_path,
        options=options,
    )


def tab_configuration_from_cli(cli_line: str, catalog: Catalog, *, tab_id: str, title: Optional[str] = None) -> TabConfiguration:
    """Build a TabConfiguration from a CLI string."""
    parsed = parse_cli_command(cli_line, catalog)
    computed_title = title or parsed.output_path.stem or tab_id
    return TabConfiguration(
        tab_id=tab_id,
        title=computed_title,
        input_pdf=parsed.input_path,
        output_epub=parsed.output_path,
        options=parsed.options,
    )

"""Helpers to translate stored configuration options into CLI arguments."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set, Tuple

from core.configuration import TabConfiguration
from core.options.catalog import Catalog, OptionMetadata

__all__ = ["build_option_arguments", "build_convert_command"]

_FORBIDDEN_FLAGS = {"--help", "-h"}
_EPUB_ONLY_PREFIX = "--epub-"


def _normalize_items(config: TabConfiguration, catalog: Catalog) -> Iterable[Tuple[OptionMetadata, object]]:
    items: List[Tuple[str, OptionMetadata, object]] = []
    for option_id, value in config.options.items():
        metadata = catalog.option_by_id(option_id)
        if metadata is None:
            continue
        items.append((metadata.cli, metadata, value))

    for _, metadata, value in sorted(items, key=lambda item: item[0]):
        yield metadata, value


def _coerce_repeatable_boolean(metadata: OptionMetadata, value: object) -> Sequence[str]:
    try:
        count = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        count = 0

    if count <= 0:
        return ()

    return tuple(metadata.cli for _ in range(count))


def _coerce_boolean(metadata: OptionMetadata, value: object) -> Sequence[str]:
    if bool(value):
        return (metadata.cli,)
    return ()


def _coerce_scalar(metadata: OptionMetadata, value: object) -> Sequence[str]:
    if value is None or value == "":
        return ()
    return (metadata.cli, str(value))


def _coerce_repeatable_scalar(metadata: OptionMetadata, value: object) -> Sequence[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        args: List[str] = []
        for item in value:
            if item in (None, ""):
                continue
            args.extend((metadata.cli, str(item)))
        return tuple(args)
    return _coerce_scalar(metadata, value)


def _option_to_args(metadata: OptionMetadata, value: object) -> Sequence[str]:
    if metadata.value_type == "boolean":
        if metadata.repeatable:
            return _coerce_repeatable_boolean(metadata, value)
        return _coerce_boolean(metadata, value)

    if metadata.repeatable:
        return _coerce_repeatable_scalar(metadata, value)

    return _coerce_scalar(metadata, value)


def build_option_arguments(
    config: TabConfiguration,
    catalog: Catalog,
    *,
    supported_flags: Optional[Set[str]] = None,
    skipped: Optional[List[str]] = None,
    output_format: Optional[str] = None,
) -> List[str]:
    """Translate the configuration option map into CLI arguments."""
    args: List[str] = []
    format_lower = output_format.lower() if output_format else None
    for metadata, value in _normalize_items(config, catalog):
        cli_flag = metadata.cli
        if cli_flag in _FORBIDDEN_FLAGS:
            if skipped is not None:
                skipped.append(cli_flag)
            continue
        if format_lower and format_lower != "epub":
            if metadata.category == "epub_output" or cli_flag.startswith(_EPUB_ONLY_PREFIX):
                if skipped is not None:
                    skipped.append(cli_flag)
                continue
        if supported_flags is not None and cli_flag not in supported_flags:
            if skipped is not None:
                skipped.append(cli_flag)
            continue
        args.extend(_option_to_args(metadata, value))
    return args


def build_convert_command(
    executable: str,
    input_path: Path,
    output_path: Path,
    config: TabConfiguration,
    catalog: Catalog,
    *,
    supported_flags: Optional[Set[str]] = None,
    skipped: Optional[List[str]] = None,
) -> List[str]:
    """Compose the full ebook-convert command line for the given config."""
    base = [executable, str(input_path), str(output_path)]
    suffix = output_path.suffix.lower()
    if suffix:
        output_format = suffix.lstrip(".")
    else:
        output_format = "oeb"
    return base + build_option_arguments(
        config,
        catalog,
        supported_flags=supported_flags,
        skipped=skipped,
        output_format=output_format,
    )

"""Helpers to translate stored configuration options into CLI arguments."""

from __future__ import annotations

from typing import Iterable, List, Sequence, Tuple

from core.configuration import TabConfiguration
from core.options.catalog import Catalog, OptionMetadata

__all__ = ["build_option_arguments"]


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


def build_option_arguments(config: TabConfiguration, catalog: Catalog) -> List[str]:
    """Translate the configuration option map into CLI arguments."""
    args: List[str] = []
    for metadata, value in _normalize_items(config, catalog):
        args.extend(_option_to_args(metadata, value))
    return args

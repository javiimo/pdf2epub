import json
from pathlib import Path

import pytest

from core.configuration import (
    ConfigurationError,
    TabConfiguration,
    load_configuration,
    save_configuration,
)


def test_save_and_load_roundtrip_preserves_data(tmp_path: Path) -> None:
    config = TabConfiguration(
        tab_id="tab-001",
        title="Configuración principal",
        input_pdf=tmp_path / "docs" / "entrada.pdf",
        output_epub="salidas/libro.epub",
        page_range="1-10",
        options={"--base-font-size": 12, "--verbose": True},
        notes="Anotación breve",
        extras={"custom": {"foo": "bar"}},
    )

    target = tmp_path / "perfiles" / "main.json"
    save_configuration(config, target)

    loaded = load_configuration(target)

    assert loaded == config
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw["custom"] == {"foo": "bar"}


def test_from_dict_requires_tab_id_and_title() -> None:
    with pytest.raises(ConfigurationError):
        TabConfiguration.from_dict({"title": "Sin ID"})

    with pytest.raises(ConfigurationError):
        TabConfiguration.from_dict({"tab_id": "solo-id"})


def test_tab_configuration_rejects_invalid_options_type() -> None:
    with pytest.raises(ConfigurationError):
        TabConfiguration(tab_id="x", title="Título", options=["--flag"])


def test_load_configuration_reports_invalid_json(tmp_path: Path) -> None:
    target = tmp_path / "bad.json"
    target.write_text("{malformado", encoding="utf-8")

    with pytest.raises(ConfigurationError):
        load_configuration(target)


def test_load_configuration_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        load_configuration(tmp_path / "no-existe.json")


def test_extras_cannot_shadow_reserved_keys() -> None:
    with pytest.raises(ConfigurationError):
        TabConfiguration(
            tab_id="dup",
            title="Duplicado",
            extras={"title": "No permitido"},
        )


def test_blank_page_range_is_normalised_to_none() -> None:
    config = TabConfiguration(tab_id="t1", title="T", page_range="   ")
    assert config.page_range is None

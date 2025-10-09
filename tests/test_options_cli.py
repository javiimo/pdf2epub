from core.configuration import TabConfiguration
from core.options.catalog import get_catalog
from core.runner.options_cli import build_option_arguments


def test_build_option_arguments_handles_various_types():
    catalog = get_catalog()
    config = TabConfiguration(
        tab_id="t1",
        title="Test",
        options={
            "base-font-size": "14",
            "enable-heuristics": True,
            "verbose": 2,
            "unknown-option": "ignored",
        },
    )

    args = build_option_arguments(config, catalog)

    assert args == [
        "--base-font-size",
        "14",
        "--enable-heuristics",
        "--verbose",
        "--verbose",
    ]

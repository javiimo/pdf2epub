from pathlib import Path

from core.configuration import TabConfiguration
from core.options.catalog import get_catalog
from core.runner.options_cli import build_convert_command, build_option_arguments


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


def test_build_convert_command_combines_base_and_options():
    catalog = get_catalog()
    config = TabConfiguration(tab_id="t2", title="Cmd", options={"base-font-size": "13"})

    command = build_convert_command(
        "ebook-convert",
        Path("input.pdf"),
        Path("output.epub"),
        config,
        catalog,
    )

    assert command[:3] == ["ebook-convert", "input.pdf", "output.epub"]
    assert command[3:] == ["--base-font-size", "13"]


def test_build_option_arguments_skip_unsupported_flags():
    catalog = get_catalog()
    config = TabConfiguration(
        tab_id="skip",
        title="Skip",
        options={"dont-split-on-page-breaks": True, "base-font-size": "11", "help": True},
    )

    skipped: list[str] = []
    args = build_option_arguments(
        config,
        catalog,
        supported_flags={"--base-font-size"},
        skipped=skipped,
    )

    assert args == ["--base-font-size", "11"]
    assert skipped == ["--dont-split-on-page-breaks", "--help"]


def test_build_convert_command_omits_epub_flags_for_oeb_output():
    catalog = get_catalog()
    config = TabConfiguration(
        tab_id="oeb",
        title="OEB Preview",
        options={"epub-version": "3", "base-font-size": "12"},
    )

    skipped: list[str] = []
    command = build_convert_command(
        "ebook-convert",
        Path("input.pdf"),
        Path("preview-oeb"),
        config,
        catalog,
        skipped=skipped,
    )

    assert "--base-font-size" in command
    assert "--epub-version" not in command
    assert skipped == ["--epub-version"]


def test_build_convert_command_skips_epub_output_category_for_oeb():
    catalog = get_catalog()
    config = TabConfiguration(
        tab_id="oeb2",
        title="OEB Preview 2",
        options={"dont-split-on-page-breaks": True, "base-font-size": "12"},
    )

    skipped: list[str] = []
    command = build_convert_command(
        "ebook-convert",
        Path("input.pdf"),
        Path("preview-oeb"),
        config,
        catalog,
        skipped=skipped,
    )

    assert "--base-font-size" in command
    assert "--dont-split-on-page-breaks" not in command
    assert "--dont-split-on-page-breaks" in skipped

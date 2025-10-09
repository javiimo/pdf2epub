import pytest

from core.options.catalog import get_catalog
from core.runner.cli_parser import (
    CliParseError,
    parse_cli_command,
    parse_cli_commands,
    parse_cli_parts,
)


def test_parse_cli_parts_validates_command_prefix():
    catalog = get_catalog()
    with pytest.raises(CliParseError):
        parse_cli_parts(["ls", "file"], catalog)


def test_parse_cli_commands_splits_multiple_invocations():
    catalog = get_catalog()
    line = (
        "ebook-convert in1.pdf out1.epub --verbose "
        "ebook-convert in2.pdf out2.epub --base-font-size 12"
    )
    results = parse_cli_commands(line, catalog)
    assert len(results) == 2
    assert results[0].input_path.name == "in1.pdf"
    assert results[1].output_path.name == "out2.epub"
    assert results[1].options["base-font-size"] == "12"


def test_parse_cli_command_rejects_multiple():
    catalog = get_catalog()
    multi = "ebook-convert in.pdf out.epub ebook-convert in2.pdf out2.epub"
    with pytest.raises(CliParseError):
        parse_cli_command(multi, catalog)


def test_parse_cli_commands_require_prefix():
    catalog = get_catalog()
    with pytest.raises(CliParseError):
        parse_cli_commands("--help", catalog)

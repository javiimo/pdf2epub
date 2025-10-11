from core.options.catalog import (
    Catalog,
    CategoryMetadata,
    OptionMetadata,
    augment_with_detected_options,
    hide_unsupported_options,
)
from core.runner.cli_support import CliSupportInfo, _extract_help_entries


def _catalog_with_options(options):
    category = CategoryMetadata(id="general", label="General", description="")
    return Catalog(
        version=1,
        categories={"general": category},
        options={opt.id: opt for opt in options},
    )


def test_hide_unsupported_options_marks_hidden():
    option_supported = OptionMetadata(
        id="supported",
        cli="--supported",
        value_type="boolean",
        domain=None,
        description="Supported option",
        category="general",
        depends_on=(),
        aliases=(),
        notes=None,
        repeatable=False,
        hidden=False,
    )
    option_unsupported = OptionMetadata(
        id="unsupported",
        cli="--unsupported",
        value_type="boolean",
        domain=None,
        description="Unsupported option",
        category="general",
        depends_on=(),
        aliases=(),
        notes=None,
        repeatable=False,
        hidden=False,
    )

    catalog = _catalog_with_options([option_supported, option_unsupported])
    filtered, hidden = hide_unsupported_options(catalog, {"--supported"})

    assert filtered.options["supported"].hidden is False
    assert filtered.options["unsupported"].hidden is True

    assert tuple(opt.id for opt in hidden) == ("unsupported",)


def test_hide_unsupported_options_removes_dependents():
    option_parent = OptionMetadata(
        id="parent",
        cli="--parent",
        value_type="boolean",
        domain=None,
        description="Parent option",
        category="general",
        depends_on=(),
        aliases=(),
        notes=None,
        repeatable=False,
        hidden=False,
    )
    option_child = OptionMetadata(
        id="child",
        cli="--child",
        value_type="boolean",
        domain=None,
        description="Child option",
        category="general",
        depends_on=("--parent",),
        aliases=(),
        notes=None,
        repeatable=False,
        hidden=False,
    )

    catalog = _catalog_with_options([option_parent, option_child])
    filtered, hidden = hide_unsupported_options(catalog, {"--child"})

    assert filtered.options["parent"].hidden is True
    assert filtered.options["child"].hidden is True

    assert {opt.id for opt in hidden} == {"parent", "child"}


def test_augment_with_detected_options_adds_new_entries():
    catalog = _catalog_with_options([])
    info = CliSupportInfo(
        flags={"--new-flag"},
        help_by_flag={"--new-flag": "--new-flag <value>    New flag"},
        raw_output="--new-flag <value>    New flag",
    )

    augmented, detected = augment_with_detected_options(catalog, info)

    assert any(option.cli == "--new-flag" for option in detected)
    option = augmented.option_by_cli("--new-flag")
    assert option is not None
    assert option.category == "detected"
    assert "New flag" in option.description


def test_augment_with_detected_options_skips_known_flags():
    existing = OptionMetadata(
        id="known",
        cli="--known",
        value_type="boolean",
        domain=None,
        description="",
        category="general",
        depends_on=(),
    )
    catalog = _catalog_with_options([existing])
    info = CliSupportInfo(
        flags={"--known"},
        help_by_flag={"--known": "--known    Known"},
        raw_output="--known    Known",
    )

    augmented, detected = augment_with_detected_options(catalog, info)

    assert augmented is catalog
    assert detected == ()


def test_extract_help_entries_handles_short_and_long_flags():
    sample = """
Options:
  --version             show program's version number and exit

  -h, --help            show this help message and exit

  --input-profile=INPUT_PROFILE
                        Specify the input profile.

    """
    entries = _extract_help_entries(sample)
    assert "--version" in entries
    assert "--help" in entries
    assert "--input-profile" in entries

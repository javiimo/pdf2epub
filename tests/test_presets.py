from core.presets import Preset, PresetConflict, apply_presets, apply_preset, get_presets


def test_get_presets_returns_presets():
    presets = get_presets()
    assert presets
    ids = {preset.id for preset in presets}
    assert "base-kobo" in ids
    assert "toc-robusto-es" in ids


def test_apply_presets_reports_conflicts_and_last_wins():
    preset_a = Preset(
        id="a",
        name="A",
        description="",
        layer="base",
        options={"output-profile": "kobo"},
    )
    preset_b = Preset(
        id="b",
        name="B",
        description="",
        layer="base",
        options={"output-profile": "tablet"},
    )
    options = {}

    report = apply_presets(options, (preset_a, preset_b))

    assert options["output-profile"] == "tablet"
    assert len(report.conflicts) == 1
    conflict: PresetConflict = report.conflicts[0]
    assert conflict.option_id == "output-profile"
    assert conflict.overridden_preset == "A"
    assert conflict.winning_preset == "B"


def test_apply_presets_merges_xpath_and_css():
    preset_a = Preset(
        id="chapter-h1",
        name="Capítulos H1",
        description="",
        layer="toc",
        options={
            "chapter": "//h:h1",
            "extra-css": "h1{color:red;}",
            "filter-css": "margin",
        },
    )
    preset_b = Preset(
        id="chapter-h2",
        name="Capítulos H2",
        description="",
        layer="toc",
        options={
            "chapter": "//h:h2",
            "extra-css": "h2{color:blue;}",
            "filter-css": "padding",
        },
    )
    options: dict[str, object] = {}

    apply_presets(options, (preset_a, preset_b))

    assert options["chapter"] == "//h:h1 | //h:h2"
    assert options["extra-css"] == "h1{color:red;}\nh2{color:blue;}"
    assert options["filter-css"] == "margin,padding"


def test_apply_preset_strips_disable_flags_without_heuristics():
    preset = Preset(
        id="cleanup",
        name="Cleanup",
        description="",
        layer="cleanup",
        options={"disable-fix-indents": True},
    )
    options: dict[str, object] = {}

    report = apply_preset(options, preset)

    assert "disable-fix-indents" not in options
    assert report.notes
    assert "enable-heuristics" in report.notes[0]

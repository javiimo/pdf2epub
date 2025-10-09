from core.presets import Preset, apply_preset, get_presets


def test_get_presets_returns_presets():
    presets = get_presets()
    assert presets
    ids = {preset.id for preset in presets}
    assert "device-kindle" in ids
    assert "pdf-technical" in ids


def test_apply_preset_merges_options():
    preset = Preset(
        id="demo",
        name="Demo",
        description="",
        category="misc",
        options={"base-font-size": "14", "line-height": "1.4"},
    )
    data = {"output-profile": "tablet"}

    apply_preset(data, preset)

    assert data["output-profile"] == "tablet"
    assert data["base-font-size"] == "14"
    assert data["line-height"] == "1.4"

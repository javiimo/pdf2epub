import pytest

from core.runner.dependencies import (
    MissingDependencyError,
    REQUIRED_BINARIES,
    verify_required_binaries,
)


def test_verify_required_binaries_returns_mapping():
    expected_paths = {
        "ebook-convert": "/usr/bin/ebook-convert",
        "qpdf": "/usr/bin/qpdf",
    }

    def fake_which(binary: str) -> str:
        return expected_paths[binary]

    assert verify_required_binaries(which=fake_which) == expected_paths


def test_verify_required_binaries_alerts_when_missing():
    captured = {}

    def fake_alert(message: str, missing: dict) -> None:
        captured["message"] = message
        captured["missing"] = missing

    def fake_which(binary: str):
        if binary == "qpdf":
            return None
        return "/usr/bin/ebook-convert"

    with pytest.raises(MissingDependencyError):
        verify_required_binaries(alert_callback=fake_alert, which=fake_which)

    assert captured["missing"] == {"qpdf": REQUIRED_BINARIES["qpdf"]}
    assert "qpdf" in captured["message"]
    assert "ebook-convert" not in captured["message"]


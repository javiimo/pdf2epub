from pathlib import Path
import subprocess

from core.runner.rasterize import (
    CropRect,
    RasterizeError,
    build_pdftocairo_png_command,
    rasterize_pdf_to_png,
)


def test_build_pdftocairo_command_full_page(tmp_path: Path):
    pdf = Path("/tmp/input.pdf")
    out = tmp_path / "out"

    cmd = build_pdftocairo_png_command(pdf, out, dpi=360)

    assert cmd[:4] == ["pdftocairo", "-png", "-r", "360"]
    # No crop flags present
    assert "-x" not in cmd and "-W" not in cmd
    # Ends with input and output prefix
    assert cmd[-2:] == [str(pdf), str(out)]


def test_build_pdftocairo_command_with_crop_and_range(tmp_path: Path):
    pdf = Path("/tmp/input.pdf")
    out = tmp_path / "prefix"
    crop = CropRect(x=10, y=20, width=300, height=200)

    cmd = build_pdftocairo_png_command(
        pdf, out, dpi=420, crop=crop, first_page=2, last_page=5
    )

    # DPI and range flags are present
    assert "-r" in cmd and cmd[cmd.index("-r") + 1] == "420"
    assert "-f" in cmd and cmd[cmd.index("-f") + 1] == "2"
    assert "-l" in cmd and cmd[cmd.index("-l") + 1] == "5"
    # Crop flags are present with integer values
    assert "-x" in cmd and "-y" in cmd and "-W" in cmd and "-H" in cmd
    assert cmd[-2:] == [str(pdf), str(out)]


def test_rasterize_invokes_subprocess_and_returns_prefix(tmp_path: Path):
    pdf = tmp_path / "fake.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% not a real PDF for unit test\n")
    out = tmp_path / "out"

    recorded = {}

    def fake_run(command, check, capture_output, text):
        recorded["command"] = command
        # Simulate successful execution
        return subprocess.CompletedProcess(command, 0, "", "")

    prefix = rasterize_pdf_to_png(pdf, out, dpi=360, run=fake_run)
    assert prefix == out
    # We passed check=True and captured outputs
    assert recorded["command"][0] == "pdftocairo"


def test_rasterize_wraps_oserror(tmp_path: Path):
    pdf = tmp_path / "in.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    out = tmp_path / "out"

    def fake_run(*_, **__):
        raise OSError("pdftocairo missing")

    try:
        rasterize_pdf_to_png(pdf, out, run=fake_run)
    except RasterizeError as exc:
        assert "pdftocairo" in str(exc)
    else:  # pragma: no cover - defensive
        assert False, "RasterizeError not raised"

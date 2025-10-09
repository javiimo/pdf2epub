from pathlib import Path

import pytest

from core.runner.pdf_subset import PdfSubsetError, prepare_pdf_subset


def test_prepare_pdf_subset_returns_original_when_no_range(tmp_path):
    original = tmp_path / "input.pdf"
    original.touch()

    result = prepare_pdf_subset(
        original,
        workspace=tmp_path / "workspace",
        page_range=None,
        qpdf_path="qpdf",
        run=lambda *_, **__: None,
    )

    assert result == original


def test_prepare_pdf_subset_invokes_qpdf_with_expected_command(tmp_path):
    original = tmp_path / "book.pdf"
    original.touch()
    workspace = tmp_path / "ws"
    recorded = {}

    def fake_run(cmd, check):
        recorded["cmd"] = cmd
        recorded["check"] = check
        # Simulate qpdf output
        output_path = Path(cmd[2])
        output_path.write_bytes(b"%PDF-1.4 subset")

    result = prepare_pdf_subset(
        original,
        workspace=workspace,
        page_range="1-5,8,10",
        qpdf_path="/usr/bin/qpdf",
        run=fake_run,
    )

    assert result == workspace / "book-subset.pdf"
    assert result.exists()
    assert recorded["cmd"] == [
        "/usr/bin/qpdf",
        str(original),
        str(result),
        "--pages",
        str(original),
        "1-5,8,10",
        "--",
    ]
    assert recorded["check"] is True


def test_prepare_pdf_subset_raises_when_qpdf_fails(tmp_path):
    original = tmp_path / "bad.pdf"
    original.touch()

    def failing_run(*args, **kwargs):
        raise OSError("not found")

    with pytest.raises(PdfSubsetError) as excinfo:
        prepare_pdf_subset(
            original,
            workspace=tmp_path / "workspace",
            page_range="1-3",
            run=failing_run,
        )

    assert "qpdf" in str(excinfo.value)

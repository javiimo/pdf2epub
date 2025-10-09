import tkinter as tk

import pytest

from app.html_viewer import HtmlViewer


@pytest.fixture
def root():
    try:
        app = tk.Tk()
        app.withdraw()
    except tk.TclError as exc:  # pragma: no cover - guard
        pytest.skip(f"Tk no disponible: {exc}")
    yield app
    app.destroy()


def test_html_viewer_loads_file(root, tmp_path):
    widget = HtmlViewer(root)
    widget.pack()
    html_path = tmp_path / "doc.xhtml"
    html_path.write_text("<html>hola</html>", encoding="utf-8")

    widget.load(html_path)
    assert widget.last_path == html_path

    fallback = getattr(widget, "_fallback", None)
    if fallback is not None:
        text = fallback.get("1.0", tk.END)
        assert "hola" in text


def test_html_viewer_reset(root, tmp_path):
    widget = HtmlViewer(root)
    widget.pack()
    html_path = tmp_path / "doc.xhtml"
    html_path.write_text("<html>hola</html>", encoding="utf-8")
    widget.load(html_path)

    widget.reset()
    assert widget.last_path is None
    fallback = getattr(widget, "_fallback", None)
    if fallback is not None:
        assert fallback.get("1.0", tk.END).strip() == ""

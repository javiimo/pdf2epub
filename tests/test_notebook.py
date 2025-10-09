import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import tkinter as tk

from app.notebook import ConfigNotebook
from core.configuration import TabConfiguration
from core.options.catalog import get_catalog
from core.presets import Preset
from core.runner.preview import PreviewError, PreviewResult
from core.runner.epub import ConversionError, ConversionResult


@pytest.fixture
def root():
    try:
        app = tk.Tk()
        app.withdraw()
    except tk.TclError as exc:  # pragma: no cover - guard for headless CI
        pytest.skip(f"Tk no disponible: {exc}")
    yield app
    app.destroy()


@pytest.fixture
def prompts():
    class PromptHelper:
        def __init__(self):
            self.cli_values = []
            self.export_path = None
            self.errors = []

        def cli_prompt(self):
            if self.cli_values:
                return self.cli_values.pop(0)
            return None

        def export_prompt(self, config: TabConfiguration):
            return str(self.export_path) if self.export_path else None

        def error_handler(self, message: str):
            self.errors.append(message)

    return PromptHelper()


def build_notebook(
    root,
    prompts,
    preview_runner=None,
    conversion_runner=None,
    presets=None,
    preset_selector=None,
):
    widget = ConfigNotebook(
        root,
        catalog=get_catalog(),
        cli_prompt=prompts.cli_prompt,
        export_prompt=prompts.export_prompt,
        error_handler=prompts.error_handler,
        preview_runner=preview_runner,
        conversion_runner=conversion_runner,
        presets=presets,
        preset_selector=preset_selector,
    )
    root.update_idletasks()
    return widget


def wait_for_jobs(root, notebook, timeout=2.0):
    deadline = time.time() + timeout
    while notebook.has_running_job():
        root.update()
        if time.time() > deadline:
            raise RuntimeError("Timeout esperando a que finalicen los trabajos en segundo plano")
    root.update()


def test_new_tab_creates_initial_configuration(root, prompts):
    notebook = build_notebook(root, prompts)
    configs = list(notebook.configurations())
    assert len(configs) == 1
    current = notebook.current_configuration()
    assert current is not None
    assert current in configs
    assert current.title.startswith("Configuración")


def test_clone_current_tab_creates_independent_copy(root, prompts):
    notebook = build_notebook(root, prompts)
    current = notebook.current_configuration()
    assert current is not None
    current.options["sample"] = "value"

    clone_id = notebook.clone_current_tab()
    assert clone_id is not None

    cloned = notebook.current_configuration()
    assert cloned is not None
    assert cloned.tab_id != current.tab_id
    assert cloned.options == current.options
    assert cloned.options is not current.options
    assert cloned.title != current.title


def test_import_cli_line_creates_tab_from_command(root, prompts):
    prompts.cli_values.append(
        "ebook-convert input.pdf output.epub --base-font-size 12 --verbose"
    )
    notebook = build_notebook(root, prompts)

    tab_id = notebook.import_cli_line()
    assert tab_id is not None

    config = notebook.current_configuration()
    assert config is not None
    assert Path(config.input_pdf) == Path("input.pdf")
    assert Path(config.output_epub) == Path("output.epub")
    assert config.options["base-font-size"] == "12"
    assert config.options["verbose"] == 1


def test_import_cli_line_reports_errors(root, prompts):
    prompts.cli_values.append("ebook-convert input.pdf")
    notebook = build_notebook(root, prompts)

    result = notebook.import_cli_line()
    assert result is None
    assert prompts.errors


def test_import_cli_line_supports_multiple_commands(root, prompts):
    prompts.cli_values.append(
        "\n".join([
            "ebook-convert in1.pdf out1.epub --verbose",
            "ebook-convert in2.pdf out2.epub --base-font-size 14",
        ])
    )
    notebook = build_notebook(root, prompts)

    tabs_before = len(notebook.notebook.tabs())
    notebook.import_cli_line()
    tabs_after = len(notebook.notebook.tabs())

    assert tabs_after == tabs_before + 2

    configs = list(notebook.configurations())
    assert any((cfg.input_pdf and cfg.input_pdf.name == "in1.pdf") for cfg in configs)
    assert any(cfg.options.get("base-font-size") == "14" for cfg in configs)


def test_apply_preset_updates_options(root, prompts):
    presets = [
        Preset(
            id="test",
            name="Kindle",
            description="",
            category="device",
            options={"output-profile": "kindle_pw", "base-font-size": "13"},
        )
    ]

    def selector(items):
        return items[0]

    notebook = build_notebook(root, prompts, presets=presets, preset_selector=selector)
    tab_id = notebook.notebook.select()

    notebook.apply_preset()
    root.update_idletasks()

    config = notebook.current_configuration()
    assert config is not None
    assert config.options["output-profile"] == "kindle_pw"
    assert config.options["base-font-size"] == "13"

    form = notebook._forms[tab_id]
    field = form.sections["profiles"].fields["output-profile"]
    assert field.var.get() == "kindle_pw"
    status = notebook._status_labels[tab_id].cget("text")
    assert "Preset aplicado" in status


def test_save_preset_creates_custom_entry(root, prompts, monkeypatch):
    notebook = build_notebook(root, prompts)
    tab_id = notebook.notebook.select()
    config = notebook.current_configuration()
    assert config is not None
    config.options["base-font-size"] = "16"

    monkeypatch.setattr("app.notebook.simpledialog.askstring", lambda *a, **k: "Mi preset")

    existing = len(notebook._presets)
    notebook.save_preset()
    root.update_idletasks()

    assert len(notebook._presets) == existing + 1
    created = notebook._presets[-1]
    assert created.name == "Mi preset"
    assert created.options["base-font-size"] == "16"
    status = notebook._status_labels[tab_id].cget("text")
    assert "Preset guardado" in status

def test_export_current_tab_writes_configuration(tmp_path, root, prompts):
    prompts.export_path = tmp_path / "config.json"
    notebook = build_notebook(root, prompts)
    config = notebook.current_configuration()
    assert config is not None
    config.title = "Principal"
    notebook.refresh_tab(notebook.notebook.select())

    exported_path = notebook.export_current_tab()
    assert exported_path == prompts.export_path
    data = json.loads(prompts.export_path.read_text(encoding="utf-8"))
    assert data["title"] == "Principal"
    assert data["tab_id"] == config.tab_id


def test_form_changes_update_configuration(root, prompts):
    notebook = build_notebook(root, prompts)
    tab_id = notebook.notebook.select()
    form = notebook._forms[tab_id]
    field = form.sections["look_and_feel"].fields["base-font-size"]
    field.var.set("14")
    root.update_idletasks()
    config = notebook.current_configuration()
    assert config.options["base-font-size"] == "14"


def test_boolean_option_toggle_updates_configuration(root, prompts):
    notebook = build_notebook(root, prompts)
    tab_id = notebook.notebook.select()
    form = notebook._forms[tab_id]
    field = form.sections["heuristics"].fields["enable-heuristics"]
    field.widget.invoke()
    root.update_idletasks()
    config = notebook.current_configuration()
    assert config.options["enable-heuristics"] is True


def test_preview_without_input_shows_error(root, prompts):
    notebook = build_notebook(root, prompts)
    tab_id = notebook.notebook.select()

    notebook.preview_current_tab()
    wait_for_jobs(root, notebook)

    assert prompts.errors
    console = notebook._console_widgets[tab_id]
    text = console.get("1.0", tk.END)
    assert "[ERROR]" in text


def test_preview_success_updates_console_and_state(tmp_path, root, prompts):
    cleanup_calls: list[str] = []

    def make_workspace(label: str):
        path = tmp_path / label
        path.mkdir()

        def cleanup():
            cleanup_calls.append(label)

        return SimpleNamespace(path=path, cleanup=cleanup)

    def preview_runner(config: TabConfiguration) -> PreviewResult:
        workspace = make_workspace("ws1")
        oeb_dir = tmp_path / "oeb-dir"
        oeb_dir.mkdir(exist_ok=True)
        html_path = oeb_dir / "chapter.xhtml"
        html_path.write_text("<html/>", encoding="utf-8")
        return PreviewResult(
            workspace=workspace,
            command=["ebook-convert", "input.pdf", "output"],
            oeb_output=oeb_dir,
            subset_pdf=tmp_path / "subset.pdf",
            stdout="todo bien",
            stderr="",
            spine_first_html=html_path,
        )

    notebook = build_notebook(root, prompts, preview_runner=preview_runner)
    tab_id = notebook.notebook.select()
    config = notebook.current_configuration()
    assert config is not None
    config.input_pdf = tmp_path / "book.pdf"

    notebook.preview_current_tab()
    wait_for_jobs(root, notebook)

    console = notebook._console_widgets[tab_id]
    text = console.get("1.0", tk.END)
    assert "[OK]" in text
    assert "todo bien" in text
    assert "Primer HTML" in text
    assert notebook._preview_state[tab_id].oeb_output == tmp_path / "oeb-dir"
    viewer = notebook._viewer_widgets[tab_id]
    assert viewer.last_path == tmp_path / "oeb-dir" / "chapter.xhtml"
    assert cleanup_calls == []
    status = notebook._status_labels[tab_id].cget("text")
    assert "Previsualización" in status
    assert "Warnings" in status


def test_preview_replaces_previous_workspace(tmp_path, root, prompts):
    cleanup_calls: list[str] = []

    def make_result(label: str) -> PreviewResult:
        path = tmp_path / label
        path.mkdir()

        def cleanup():
            cleanup_calls.append(label)

        workspace = SimpleNamespace(path=path, cleanup=cleanup)
        html_path = path / "chapter.xhtml"
        html_path.write_text("<html/>", encoding="utf-8")
        return PreviewResult(
            workspace=workspace,
            command=["ebook-convert", "input.pdf", label],
            oeb_output=tmp_path / f"{label}-oeb",
            subset_pdf=tmp_path / f"{label}-subset.pdf",
            stdout=label,
            stderr="",
            spine_first_html=html_path,
        )

    results = [make_result("ws1"), make_result("ws2")]

    def preview_runner(config: TabConfiguration) -> PreviewResult:
        return results.pop(0)

    notebook = build_notebook(root, prompts, preview_runner=preview_runner)
    tab_id = notebook.notebook.select()
    config = notebook.current_configuration()
    assert config is not None
    config.input_pdf = tmp_path / "doc.pdf"

    notebook.preview_current_tab()
    wait_for_jobs(root, notebook)
    assert cleanup_calls == []

    notebook.preview_current_tab()
    wait_for_jobs(root, notebook)
    assert cleanup_calls == ["ws1"]
    assert notebook._preview_state[tab_id].workspace.path == tmp_path / "ws2"


def test_preview_failure_shows_console(tmp_path, root, prompts):
    def preview_runner(config: TabConfiguration):
        raise PreviewError(
            "Falló",
            command=["ebook-convert", "input.pdf", "output"],
            stdout="stdout msg",
            stderr="stderr msg",
            returncode=1,
        )

    notebook = build_notebook(root, prompts, preview_runner=preview_runner)
    tab_id = notebook.notebook.select()
    config = notebook.current_configuration()
    assert config is not None
    config.input_pdf = tmp_path / "doc.pdf"

    notebook.preview_current_tab()
    wait_for_jobs(root, notebook)

    assert prompts.errors
    console = notebook._console_widgets[tab_id]
    text = console.get("1.0", tk.END)
    assert "[ERROR]" in text
    assert "stderr msg" in text


def test_generate_epub_updates_config_and_console(tmp_path, root, prompts):
    target = tmp_path / "salida.epub"

    def conversion_runner(config: TabConfiguration, destination: Path) -> ConversionResult:
        assert destination == target
        destination.write_text("ebook", encoding="utf-8")
        return ConversionResult(
            command=["ebook-convert", "input.pdf", str(destination)],
            target=destination,
            subset_pdf=tmp_path / "subset.pdf",
            stdout="todo ok",
            stderr="",
        )

    notebook = build_notebook(root, prompts, conversion_runner=conversion_runner)
    tab_id = notebook.notebook.select()
    config = notebook.current_configuration()
    assert config is not None
    config.input_pdf = tmp_path / "doc.pdf"
    config.input_pdf.write_text("pdf", encoding="utf-8")
    notebook._ask_output_epub = lambda _: str(target)

    notebook.generate_epub()
    wait_for_jobs(root, notebook)

    assert config.output_epub == target
    summary = notebook._summary_labels[tab_id].cget("text")
    assert str(target) in summary
    console = notebook._console_widgets[tab_id]
    text = console.get("1.0", tk.END)
    assert "[OK] EPUB generado" in text
    assert "todo ok" in text
    assert not prompts.errors
    status = notebook._status_labels[tab_id].cget("text")
    assert "EPUB" in status
    assert "Tamaño" in status


def test_generate_epub_reports_errors(tmp_path, root, prompts):
    def conversion_runner(config: TabConfiguration, destination: Path):
        raise ConversionError(
            "Fallo",
            command=["ebook-convert", "input.pdf", str(destination)],
            stdout="out",
            stderr="err",
            returncode=2,
        )

    notebook = build_notebook(root, prompts, conversion_runner=conversion_runner)
    tab_id = notebook.notebook.select()
    config = notebook.current_configuration()
    assert config is not None
    config.input_pdf = tmp_path / "doc.pdf"
    config.input_pdf.write_text("pdf", encoding="utf-8")
    notebook._ask_output_epub = lambda _: str(tmp_path / "salida.epub")

    notebook.generate_epub()
    wait_for_jobs(root, notebook)

    assert prompts.errors
    console = notebook._console_widgets[tab_id]
    text = console.get("1.0", tk.END)
    assert "[ERROR]" in text
    assert "err" in text


def test_generate_epub_cancel_keeps_state(tmp_path, root, prompts):
    notebook = build_notebook(root, prompts)
    tab_id = notebook.notebook.select()
    config = notebook.current_configuration()
    assert config is not None
    config.input_pdf = tmp_path / "doc.pdf"
    config.input_pdf.write_text("pdf", encoding="utf-8")
    notebook._ask_output_epub = lambda _: ""

    notebook.generate_epub()
    root.update_idletasks()

    assert config.output_epub is None
    console = notebook._console_widgets[tab_id]
    assert console.get("1.0", tk.END).strip() == ""
    assert not prompts.errors


def test_generate_epub_requires_input(root, prompts):
    notebook = build_notebook(root, prompts)

    notebook.generate_epub()

    assert prompts.errors


def test_reload_preview_updates_viewer(tmp_path, root, prompts):
    def preview_runner(config: TabConfiguration) -> PreviewResult:
        path = tmp_path / "ws"
        path.mkdir()
        html_path = tmp_path / "oeb" / "page.xhtml"
        html_path.parent.mkdir(exist_ok=True)
        html_path.write_text("old", encoding="utf-8")
        return PreviewResult(
            workspace=SimpleNamespace(path=path, cleanup=lambda: None),
            command=["ebook-convert", "input.pdf", "output"],
            oeb_output=html_path.parent,
            subset_pdf=tmp_path / "subset.pdf",
            stdout="",
            stderr="",
            spine_first_html=html_path,
        )

    notebook = build_notebook(root, prompts, preview_runner=preview_runner)
    tab_id = notebook.notebook.select()
    config = notebook.current_configuration()
    assert config is not None
    config.input_pdf = tmp_path / "doc.pdf"
    config.input_pdf.write_text("pdf", encoding="utf-8")

    notebook.preview_current_tab()
    wait_for_jobs(root, notebook)

    viewer = notebook._viewer_widgets[tab_id]
    html_path = viewer.last_path
    assert html_path is not None
    html_path.write_text("new content", encoding="utf-8")

    notebook.reload_preview()
    root.update_idletasks()

    fallback = getattr(viewer, "_fallback", None)
    if fallback is not None:
        text = fallback.get("1.0", tk.END)
        assert "new content" in text
    console = notebook._console_widgets[tab_id]
    assert "Recarga" in console.get("1.0", tk.END)


def test_reload_without_preview_reports_error(root, prompts):
    notebook = build_notebook(root, prompts)
    notebook.reload_preview()
    assert prompts.errors


def test_export_oeb_writes_directory(tmp_path, root, prompts):
    oeb_dir = tmp_path / "oeb"
    oeb_dir.mkdir()
    html = oeb_dir / "index.xhtml"
    html.write_text("<html/>", encoding="utf-8")

    def preview_runner(config: TabConfiguration) -> PreviewResult:
        path = tmp_path / "ws"
        path.mkdir()
        return PreviewResult(
            workspace=SimpleNamespace(path=path, cleanup=lambda: None),
            command=["ebook-convert", "input.pdf", "preview-oeb"],
            oeb_output=oeb_dir,
            subset_pdf=tmp_path / "subset.pdf",
            stdout="ready",
            stderr="",
            spine_first_html=html,
        )

    notebook = build_notebook(root, prompts, preview_runner=preview_runner)
    tab_id = notebook.notebook.select()
    config = notebook.current_configuration()
    assert config is not None
    config.input_pdf = tmp_path / "doc.pdf"
    config.input_pdf.write_text("pdf", encoding="utf-8")

    notebook.preview_current_tab()
    wait_for_jobs(root, notebook)

    export_dir = tmp_path / "exported-oeb"
    notebook._ask_oeb_directory = lambda _: str(export_dir)

    notebook.export_oeb()
    wait_for_jobs(root, notebook)
    root.update_idletasks()

    assert (export_dir / "index.xhtml").exists()
    console = notebook._console_widgets[tab_id]
    text = console.get("1.0", tk.END)
    assert "OEB exportado" in text
    status = notebook._status_labels[tab_id].cget("text")
    assert "OEB exportado" in status


def test_export_oeb_runs_preview_when_missing(tmp_path, root, prompts):
    calls: list[str] = []

    def preview_runner(config: TabConfiguration) -> PreviewResult:
        calls.append("preview")
        oeb_dir = tmp_path / "pre"
        oeb_dir.mkdir()
        html = oeb_dir / "page.xhtml"
        html.write_text("<html/>", encoding="utf-8")
        return PreviewResult(
            workspace=SimpleNamespace(path=tmp_path / "ws", cleanup=lambda: None),
            command=["ebook-convert", "input.pdf", "preview-oeb"],
            oeb_output=oeb_dir,
            subset_pdf=tmp_path / "subset.pdf",
            stdout="ok",
            stderr="",
            spine_first_html=html,
        )

    notebook = build_notebook(root, prompts, preview_runner=preview_runner)
    config = notebook.current_configuration()
    assert config is not None
    config.input_pdf = tmp_path / "doc.pdf"
    config.input_pdf.write_text("pdf", encoding="utf-8")

    export_dir = tmp_path / "dest"
    notebook._ask_oeb_directory = lambda _: str(export_dir)

    notebook.export_oeb()
    wait_for_jobs(root, notebook)
    root.update_idletasks()

    assert calls == ["preview"]
    assert (export_dir / "page.xhtml").exists()


def test_export_oeb_requires_empty_directory(tmp_path, root, prompts):
    oeb_dir = tmp_path / "oeb"
    oeb_dir.mkdir()
    (oeb_dir / "file.xhtml").write_text("<html/>", encoding="utf-8")

    def preview_runner(config: TabConfiguration) -> PreviewResult:
        return PreviewResult(
            workspace=SimpleNamespace(path=tmp_path / "ws", cleanup=lambda: None),
            command=["ebook-convert", "input.pdf", "preview-oeb"],
            oeb_output=oeb_dir,
            subset_pdf=tmp_path / "subset.pdf",
            stdout="ok",
            stderr="",
            spine_first_html=oeb_dir / "file.xhtml",
        )

    notebook = build_notebook(root, prompts, preview_runner=preview_runner)
    tab_id = notebook.notebook.select()
    config = notebook.current_configuration()
    assert config is not None
    config.input_pdf = tmp_path / "doc.pdf"
    config.input_pdf.write_text("pdf", encoding="utf-8")
    notebook.preview_current_tab()
    wait_for_jobs(root, notebook)

    export_dir = tmp_path / "dest"
    export_dir.mkdir()
    (export_dir / "existing.txt").write_text("x", encoding="utf-8")
    notebook._ask_oeb_directory = lambda _: str(export_dir)

    notebook.export_oeb()
    root.update_idletasks()

    assert prompts.errors
    console = notebook._console_widgets[tab_id]
    assert "directorio destino" in console.get("1.0", tk.END).lower()


def test_export_oeb_requires_input(root, prompts):
    notebook = build_notebook(root, prompts)
    notebook.export_oeb()
    assert prompts.errors


def test_pdf_selector_updates_configuration(tmp_path, root, prompts):
    notebook = build_notebook(root, prompts)
    tab_id = notebook.notebook.select()
    controls = notebook._input_controls[tab_id]

    pdf_path = tmp_path / "documento.pdf"
    controls["path_var"].set(str(pdf_path))
    root.update_idletasks()
    config = notebook.current_configuration()
    assert config is not None
    assert config.input_pdf == pdf_path

    controls["path_var"].set("   ")
    root.update_idletasks()
    assert config.input_pdf is None


def test_page_range_entry_updates_configuration(root, prompts):
    notebook = build_notebook(root, prompts)
    tab_id = notebook.notebook.select()
    controls = notebook._input_controls[tab_id]

    controls["page_var"].set(" 5-10 ")
    root.update_idletasks()
    config = notebook.current_configuration()
    assert config is not None
    assert config.page_range == "5-10"

    controls["page_var"].set(" ")
    root.update_idletasks()
    assert config.page_range is None


def test_tooltip_text_available_for_fields(root, prompts):
    notebook = build_notebook(root, prompts)
    tab_id = notebook.notebook.select()
    form = notebook._forms[tab_id]
    field = form.sections["debug"].fields["verbose"]
    assert "verbosidad" in field.tooltip_text.lower()


def test_dependency_blocks_option_until_parent_enabled(root, prompts):
    notebook = build_notebook(root, prompts)
    tab_id = notebook.notebook.select()
    form = notebook._forms[tab_id]
    dependent = form.sections["heuristics"].fields["disable-dehyphenate"]
    assert "Activa primero" in dependent.error_label.cget("text")
    assert str(dependent.widget.cget("state")) == "disabled"

    parent = form.sections["heuristics"].fields["enable-heuristics"]
    parent.widget.invoke()
    root.update_idletasks()

    assert str(dependent.widget.cget("state")) == "normal"
    assert "Activa primero" not in dependent.error_label.cget("text")


def test_invalid_regex_shows_validation_error(root, prompts):
    notebook = build_notebook(root, prompts)
    tab_id = notebook.notebook.select()
    form = notebook._forms[tab_id]
    field = form.sections["toc"].fields["toc-filter"]
    field.var.set("[invalid")
    root.update_idletasks()
    assert "Expresión regular inválida" in field.error_label.cget("text")
    field.var.set("chapter")
    root.update_idletasks()
    assert field.error_label.cget("text") == ""


def test_path_validator_requires_existing_dir(tmp_path, root, prompts):
    notebook = build_notebook(root, prompts)
    tab_id = notebook.notebook.select()
    form = notebook._forms[tab_id]
    field = form.sections["debug"].fields["debug-pipeline"]

    missing = tmp_path / "missing-dir"
    field.var.set(str(missing))
    root.update_idletasks()
    assert "directorio" in field.error_label.cget("text").lower()

    valid = tmp_path / "existing-dir"
    valid.mkdir()
    field.var.set(str(valid))
    root.update_idletasks()
    assert field.error_label.cget("text") == ""

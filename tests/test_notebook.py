import json
from pathlib import Path

import pytest
import tkinter as tk

from app.notebook import ConfigNotebook
from core.configuration import TabConfiguration
from core.options.catalog import get_catalog


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


def build_notebook(root, prompts):
    widget = ConfigNotebook(
        root,
        catalog=get_catalog(),
        cli_prompt=prompts.cli_prompt,
        export_prompt=prompts.export_prompt,
        error_handler=prompts.error_handler,
    )
    root.update_idletasks()
    return widget


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

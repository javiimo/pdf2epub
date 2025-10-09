"""Tkinter notebook widget managing per-tab configurations."""

from __future__ import annotations

import copy
import shlex
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional
from uuid import uuid4

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from app.forms import ConfigForm
from core.configuration import ConfigurationError, TabConfiguration, save_configuration
from core.options.catalog import Catalog, get_catalog
from core.runner.cli_parser import CliParseError, tab_configuration_from_cli
from core.runner.preview import PreviewError, PreviewResult, run_preview

CliPrompt = Callable[[], Optional[str]]
ExportPrompt = Callable[[TabConfiguration], Optional[str]]
ErrorHandler = Callable[[str], None]


class ConfigNotebook(ttk.Frame):
    """Notebook widget that manages independent configuration tabs."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        catalog: Optional[Catalog] = None,
        cli_prompt: Optional[CliPrompt] = None,
        export_prompt: Optional[ExportPrompt] = None,
        error_handler: Optional[ErrorHandler] = None,
        preview_runner: Optional[Callable[[TabConfiguration], PreviewResult]] = None,
        **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)

        self.catalog = catalog or get_catalog()
        self._cli_prompt = cli_prompt or self._ask_cli_line
        self._export_prompt = export_prompt or self._ask_export_path
        self._error_handler = error_handler or self._default_error_handler

        self.toolbar = ttk.Frame(self)
        self.toolbar.grid(row=0, column=0, sticky="ew")

        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=1, column=0, sticky="nsew")

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._config_by_tab: Dict[str, TabConfiguration] = {}
        self._summary_labels: Dict[str, ttk.Label] = {}
        self._forms: Dict[str, ConfigForm] = {}
        self._console_widgets: Dict[str, tk.Text] = {}
        self._preview_state: Dict[str, PreviewResult] = {}
        self._preview_runner = preview_runner or (lambda config: run_preview(config, catalog=self.catalog))
        self._input_controls: Dict[str, Dict[str, Any]] = {}
        self._tab_counter = 1

        self._build_toolbar()
        self.new_tab()

    # -- Public API -----------------------------------------------------

    def new_tab(self) -> Optional[str]:
        """Create a brand new configuration tab."""
        tab_id = self._generate_tab_id()
        title = self._generate_title()
        config = TabConfiguration(tab_id=tab_id, title=title)
        return self._add_tab(config)

    def clone_current_tab(self) -> Optional[str]:
        tab_id = self._current_tab_id()
        if tab_id is None:
            self._error_handler("No hay pestaña seleccionada para clonar.")
            return None

        original = self._config_by_tab[tab_id]
        clone = replace(
            original,
            tab_id=self._generate_tab_id(),
            title=self._generate_clone_title(original.title),
            options=copy.deepcopy(original.options),
            extras=copy.deepcopy(original.extras),
        )
        return self._add_tab(clone)

    def import_cli_line(self) -> Optional[str]:
        line = self._cli_prompt()
        if not line:
            return None

        tab_id = self._generate_tab_id()
        try:
            config = tab_configuration_from_cli(line, self.catalog, tab_id=tab_id)
        except CliParseError as exc:
            self._error_handler(str(exc))
            return None

        config.title = self._ensure_unique_title(config.title)
        return self._add_tab(config)

    def export_current_tab(self) -> Optional[Path]:
        tab_id = self._current_tab_id()
        if tab_id is None:
            self._error_handler("No hay pestaña seleccionada para exportar.")
            return None

        config = self._config_by_tab[tab_id]
        target = self._export_prompt(config)
        if not target:
            return None

        path = Path(target)
        try:
            save_configuration(config, path)
        except ConfigurationError as exc:
            self._error_handler(str(exc))
            return None
        return path

    def current_configuration(self) -> Optional[TabConfiguration]:
        tab_id = self._current_tab_id()
        if tab_id is None:
            return None
        return self._config_by_tab[tab_id]

    def configurations(self) -> Iterable[TabConfiguration]:
        for tab_id in self.notebook.tabs():
            yield self._config_by_tab[tab_id]

    def refresh_tab(self, tab_widget_id: str) -> None:
        config = self._config_by_tab[tab_widget_id]
        self.notebook.tab(tab_widget_id, text=config.title)
        label = self._summary_labels[tab_widget_id]
        label.configure(text=self._format_summary(config))
        self._sync_input_controls(tab_widget_id)
        self._forms[tab_widget_id].sync_from_config()

    def _handle_form_change(self, tab_widget_id: str, option_id: str) -> None:
        config = self._config_by_tab[tab_widget_id]
        summary = self._summary_labels[tab_widget_id]
        summary.configure(text=self._format_summary(config))

    # -- Internal helpers ----------------------------------------------

    def _build_toolbar(self) -> None:
        buttons = [
            ("Nueva", self.new_tab),
            ("Clonar", self.clone_current_tab),
            ("Importar línea CLI", self.import_cli_line),
            ("Exportar", self.export_current_tab),
            ("Previsualizar", self.preview_current_tab),
        ]

        for idx, (label, command) in enumerate(buttons):
            button = ttk.Button(self.toolbar, text=label, command=command)
            button.grid(row=0, column=idx, padx=4, pady=4, sticky="w")
        self.toolbar.grid_columnconfigure(len(buttons), weight=1)

    def _add_tab(self, config: TabConfiguration) -> str:
        widget = ttk.Frame(self.notebook)
        widget.columnconfigure(0, weight=3)
        widget.columnconfigure(1, weight=2)
        widget.rowconfigure(2, weight=1)

        tab_widget_id = str(widget)

        summary = ttk.Label(
            widget,
            text=self._format_summary(config),
            padding=12,
            justify="left",
            anchor="nw",
        )
        summary.grid(row=0, column=0, columnspan=2, sticky="ew")

        self._create_input_controls(widget, config, tab_widget_id)
        self._create_console(widget, tab_widget_id)

        form = ConfigForm(
            widget,
            catalog=self.catalog,
            config=config,
            on_change=lambda option_id: self._handle_form_change(tab_widget_id, option_id),
        )
        form.grid(row=2, column=0, sticky="nsew", padx=(12, 6), pady=(0, 12))

        self.notebook.add(widget, text=config.title)
        self._config_by_tab[tab_widget_id] = config
        self._summary_labels[tab_widget_id] = summary
        self._forms[tab_widget_id] = form
        self.notebook.select(widget)
        return tab_widget_id

    def _current_tab_id(self) -> Optional[str]:
        selection = self.notebook.select()
        return selection or None

    def _format_summary(self, config: TabConfiguration) -> str:
        input_path = str(config.input_pdf or "Seleccionar PDF…")
        output_path = str(config.output_epub or "Sin destino EPUB")
        page_range = config.page_range or "Todas las páginas"
        options_count = len(config.options)
        return "\n".join(
            [
                f"Título: {config.title}",
                f"Entrada: {input_path}",
                f"Salida: {output_path}",
                f"Rango: {page_range}",
                f"Opciones configuradas: {options_count}",
            ]
        )

    # -- Input controls ------------------------------------------------

    def _create_input_controls(
        self,
        parent: ttk.Frame,
        config: TabConfiguration,
        tab_widget_id: str,
    ) -> None:
        frame = ttk.LabelFrame(parent, text="Entrada PDF")
        frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 12))
        frame.columnconfigure(1, weight=1)

        path_var = tk.StringVar(value=str(config.input_pdf or ""))
        page_var = tk.StringVar(value=config.page_range or "")

        controls: Dict[str, Any] = {
            "frame": frame,
            "path_var": path_var,
            "page_var": page_var,
            "suspend": False,
        }
        self._input_controls[tab_widget_id] = controls

        path_var.trace_add(
            "write",
            lambda *_ignored, tab_id=tab_widget_id: self._on_input_pdf_changed(tab_id),
        )
        page_var.trace_add(
            "write",
            lambda *_ignored, tab_id=tab_widget_id: self._on_page_range_changed(tab_id),
        )

        ttk.Label(frame, text="Archivo PDF").grid(row=0, column=0, sticky="w", padx=(8, 6), pady=4)
        entry = ttk.Entry(frame, textvariable=path_var)
        entry.grid(row=0, column=1, sticky="ew", padx=(0, 6), pady=4)
        browse = ttk.Button(
            frame,
            text="Seleccionar…",
            command=lambda tab_id=tab_widget_id: self._browse_input_pdf(tab_id),
        )
        browse.grid(row=0, column=2, sticky="e", padx=(0, 8), pady=4)

        ttk.Label(frame, text="Rango de páginas").grid(row=1, column=0, sticky="w", padx=(8, 6), pady=4)
        page_entry = ttk.Entry(frame, textvariable=page_var)
        page_entry.grid(row=1, column=1, sticky="ew", padx=(0, 6), pady=4)
        ttk.Label(frame, text="Ej: 1-5,8,10").grid(row=1, column=2, sticky="e", padx=(0, 8), pady=4)

    def _sync_input_controls(self, tab_widget_id: str) -> None:
        controls = self._input_controls.get(tab_widget_id)
        if not controls:
            return
        controls["suspend"] = True
        try:
            config = self._config_by_tab[tab_widget_id]
            path_value = str(config.input_pdf) if config.input_pdf else ""
            page_value = config.page_range or ""
            controls["path_var"].set(path_value)
            controls["page_var"].set(page_value)
        finally:
            controls["suspend"] = False

    def _create_console(self, parent: ttk.Frame, tab_widget_id: str) -> None:
        frame = ttk.LabelFrame(parent, text="Consola")
        frame.grid(row=2, column=1, sticky="nsew", padx=(0, 12), pady=(0, 12))
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        console = tk.Text(frame, wrap="word", height=12, state="disabled")
        console.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=console.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        console.configure(yscrollcommand=scrollbar.set)

        self._console_widgets[tab_widget_id] = console

    def _append_console(self, tab_widget_id: str, message: str, *, clear: bool = False) -> None:
        console = self._console_widgets.get(tab_widget_id)
        if console is None:
            return
        console.configure(state="normal")
        if clear:
            console.delete("1.0", tk.END)
        if message:
            console.insert(tk.END, message)
            if not message.endswith("\n"):
                console.insert(tk.END, "\n")
        console.see(tk.END)
        console.configure(state="disabled")

    def _cleanup_preview_state(self, tab_widget_id: str) -> None:
        state = self._preview_state.pop(tab_widget_id, None)
        if not state:
            return
        workspace = getattr(state, "workspace", None)
        if workspace is not None and hasattr(workspace, "cleanup"):
            try:
                workspace.cleanup()
            except Exception:
                # Silently ignore workspace cleanup issues in the UI layer.
                pass

    def _format_command(self, command: Iterable[str]) -> str:
        return " ".join(shlex.quote(str(part)) for part in command)

    def _render_stream(self, label: str, content: str) -> str:
        stripped = content.strip()
        if not stripped:
            return f"{label}: <vacío>"
        return f"{label}:\n{stripped}"

    def _format_preview_success(self, result: PreviewResult) -> str:
        pieces = [
            "[OK] Previsualización completada.",
            f"Comando: {self._format_command(result.command)}",
            f"OEB generado en: {result.oeb_output}",
            self._render_stream("stdout", result.stdout),
            self._render_stream("stderr", result.stderr),
        ]
        return "\n\n".join(pieces)

    def _format_preview_error(self, error: PreviewError) -> str:
        command = error.command or []
        pieces = [
            f"[ERROR] {error}",
            f"Comando: {self._format_command(command) if command else '<no ejecutado>'}",
            self._render_stream("stdout", error.stdout),
            self._render_stream("stderr", error.stderr),
        ]
        return "\n\n".join(pieces)

    def preview_current_tab(self) -> None:
        tab_id = self._current_tab_id()
        if tab_id is None:
            self._error_handler("No hay pestaña seleccionada para previsualizar.")
            return

        config = self._config_by_tab[tab_id]
        self._append_console(tab_id, "Ejecutando previsualización…", clear=True)

        try:
            result = self._preview_runner(config)
        except PreviewError as exc:
            self._append_console(tab_id, self._format_preview_error(exc), clear=True)
            self._error_handler(str(exc))
            return

        self._cleanup_preview_state(tab_id)
        self._preview_state[tab_id] = result
        self._append_console(tab_id, self._format_preview_success(result), clear=True)

    def _on_input_pdf_changed(self, tab_widget_id: str) -> None:
        controls = self._input_controls.get(tab_widget_id)
        if not controls or controls.get("suspend"):
            return
        raw = controls["path_var"].get().strip()
        config = self._config_by_tab[tab_widget_id]
        if raw:
            config.input_pdf = Path(raw).expanduser()
        else:
            config.input_pdf = None
        summary = self._summary_labels[tab_widget_id]
        summary.configure(text=self._format_summary(config))

    def _on_page_range_changed(self, tab_widget_id: str) -> None:
        controls = self._input_controls.get(tab_widget_id)
        if not controls or controls.get("suspend"):
            return
        raw = controls["page_var"].get().strip()
        config = self._config_by_tab[tab_widget_id]
        config.page_range = raw or None
        summary = self._summary_labels[tab_widget_id]
        summary.configure(text=self._format_summary(config))

    def _browse_input_pdf(self, tab_widget_id: str) -> None:  # pragma: no cover - UI helper
        controls = self._input_controls.get(tab_widget_id)
        if not controls:
            return
        current = controls["path_var"].get().strip()
        initial = None
        if current:
            initial = str(Path(current).expanduser().parent)
        selected = filedialog.askopenfilename(
            parent=self.winfo_toplevel(),
            title="Selecciona PDF de entrada",
            initialdir=initial,
            filetypes=[("PDF", "*.pdf"), ("Todos los archivos", "*.*")],
        )
        if selected:
            controls["path_var"].set(selected)

    def _generate_tab_id(self) -> str:
        return f"tab-{uuid4().hex}"

    def _generate_title(self) -> str:
        title = f"Configuración {self._tab_counter}"
        self._tab_counter += 1
        return self._ensure_unique_title(title)

    def _existing_titles(self) -> List[str]:
        return [self.notebook.tab(tab, "text") for tab in self.notebook.tabs()]

    def _ensure_unique_title(self, desired: str) -> str:
        existing = set(self._existing_titles())
        if desired not in existing:
            return desired

        suffix = 2
        while True:
            candidate = f"{desired} ({suffix})"
            if candidate not in existing:
                return candidate
            suffix += 1

    def _generate_clone_title(self, base: str) -> str:
        return self._ensure_unique_title(f"{base} (copia)")

    def _ask_cli_line(self) -> Optional[str]:  # pragma: no cover - UI helper
        return simpledialog.askstring(
            "Importar línea CLI",
            "Ingresa la línea completa 'ebook-convert entrada salida [opciones]'",
            parent=self.winfo_toplevel(),
        )

    def _ask_export_path(self, config: TabConfiguration) -> Optional[str]:  # pragma: no cover - UI helper
        initial = f"{config.title or 'config'}.json"
        return filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("Todos los archivos", "*.*")],
            initialfile=initial,
            title="Exportar configuración",
            parent=self.winfo_toplevel(),
        )

    def _default_error_handler(self, message: str) -> None:  # pragma: no cover - UI helper
        messagebox.showerror("Error", message, parent=self.winfo_toplevel())

    def destroy(self) -> None:  # pragma: no cover - UI lifecycle
        for tab_id in list(self._preview_state.keys()):
            self._cleanup_preview_state(tab_id)
        super().destroy()

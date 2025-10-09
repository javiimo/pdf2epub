"""Tkinter notebook widget managing per-tab configurations."""

from __future__ import annotations

import copy
from dataclasses import replace
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional
from uuid import uuid4

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from core.configuration import ConfigurationError, TabConfiguration, save_configuration
from core.options.catalog import Catalog, get_catalog
from core.runner.cli_parser import CliParseError, tab_configuration_from_cli

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

    # -- Internal helpers ----------------------------------------------

    def _build_toolbar(self) -> None:
        buttons = [
            ("Nueva", self.new_tab),
            ("Clonar", self.clone_current_tab),
            ("Importar línea CLI", self.import_cli_line),
            ("Exportar", self.export_current_tab),
        ]

        for idx, (label, command) in enumerate(buttons):
            button = ttk.Button(self.toolbar, text=label, command=command)
            button.grid(row=0, column=idx, padx=4, pady=4, sticky="w")
        self.toolbar.grid_columnconfigure(len(buttons), weight=1)

    def _add_tab(self, config: TabConfiguration) -> str:
        widget = ttk.Frame(self.notebook)
        summary = ttk.Label(
            widget,
            text=self._format_summary(config),
            padding=12,
            justify="left",
            anchor="nw",
        )
        summary.pack(fill="both", expand=True)

        tab_widget_id = str(widget)
        self.notebook.add(widget, text=config.title)
        self._config_by_tab[tab_widget_id] = config
        self._summary_labels[tab_widget_id] = summary
        self.notebook.select(widget)
        return tab_widget_id

    def _current_tab_id(self) -> Optional[str]:
        selection = self.notebook.select()
        return selection or None

    def _format_summary(self, config: TabConfiguration) -> str:
        input_path = str(config.input_pdf or "Seleccionar PDF…")
        output_path = str(config.output_epub or "Sin destino EPUB")
        options_count = len(config.options)
        return "\n".join(
            [
                f"Título: {config.title}",
                f"Entrada: {input_path}",
                f"Salida: {output_path}",
                f"Opciones configuradas: {options_count}",
            ]
        )

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

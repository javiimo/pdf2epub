"""Tkinter notebook widget managing per-tab configurations."""

from __future__ import annotations

import copy
import shlex
import shutil
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional
from uuid import uuid4

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from app.forms import ConfigForm
from app.html_viewer import HtmlViewer
from app.preset_dialog import choose_preset
from core.configuration import ConfigurationError, TabConfiguration, save_configuration
from core.options.catalog import Catalog, get_catalog
from core.presets import Preset, apply_preset, get_presets
from core.runner.cli_parser import (
    CliParseError,
    CliParseResult,
    parse_cli_commands,
    tab_configuration_from_cli,
)
from core.runner.epub import ConversionError, ConversionResult, run_epub
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
        conversion_runner: Optional[Callable[[TabConfiguration, Path], ConversionResult]] = None,
        presets: Optional[Iterable[Preset]] = None,
        preset_selector: Optional[Callable[[List[Preset]], Optional[Preset]]] = None,
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
        self._viewer_widgets: Dict[str, HtmlViewer] = {}
        self._console_widgets: Dict[str, tk.Text] = {}
        self._status_labels: Dict[str, ttk.Label] = {}
        self._preview_state: Dict[str, PreviewResult] = {}
        self._preview_runner = preview_runner or (lambda config: run_preview(config, catalog=self.catalog))
        self._conversion_runner = conversion_runner or (
            lambda config, target: run_epub(config, target=target, catalog=self.catalog)
        )
        self._presets = list(presets) if presets is not None else get_presets()
        self._preset_selector = preset_selector or (lambda items: choose_preset(self, items))
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

        try:
            results = parse_cli_commands(line, self.catalog)
        except CliParseError as exc:
            self._error_handler(str(exc))
            return None

        created_tabs: List[str] = []
        for result in results:
            config = self._configuration_from_cli(result)
            widget_id = self._add_tab(config)
            created_tabs.append(widget_id)

        if not created_tabs:
            return None
        return created_tabs[-1]

    def _configuration_from_cli(self, result: CliParseResult) -> TabConfiguration:
        tab_id = self._generate_tab_id()
        title = result.output_path.stem or tab_id
        config = TabConfiguration(
            tab_id=tab_id,
            title=title,
            input_pdf=result.input_path,
            output_epub=result.output_path,
            options=result.options,
        )
        config.title = self._ensure_unique_title(config.title)
        return config

    def apply_preset(self) -> None:
        tab_id = self._current_tab_id()
        if tab_id is None:
            self._error_handler("No hay pestaña seleccionada para aplicar un preset.")
            return

        if not self._presets:
            self._error_handler("No hay presets configurados.")
            return

        preset = self._preset_selector(self._presets)
        if preset is None:
            return

        config = self._config_by_tab[tab_id]
        apply_preset(config.options, preset)
        self._forms[tab_id].sync_from_config()
        self._summary_labels[tab_id].configure(text=self._format_summary(config))
        self._append_console(tab_id, f"[OK] Preset aplicado: {preset.name}")
        self._update_status(tab_id, f"Preset aplicado: {preset.name}")

    def save_preset(self) -> None:
        tab_id = self._current_tab_id()
        if tab_id is None:
            self._error_handler("No hay pestaña seleccionada para guardar un preset.")
            return

        config = self._config_by_tab[tab_id]
        name = simpledialog.askstring(
            "Guardar preset",
            "Nombre del nuevo preset:",
            initialvalue=config.title,
            parent=self.winfo_toplevel(),
        )
        if not name:
            return

        preset = Preset(
            id=f"custom-{uuid4().hex}",
            name=name.strip(),
            description=f"Preset personalizado basado en {config.title}",
            category="custom",
            options=copy.deepcopy(config.options),
        )
        self._presets.append(preset)
        self._append_console(tab_id, f"[OK] Preset guardado: {preset.name}")
        self._update_status(tab_id, f"Preset guardado: {preset.name}")

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
            ("Presets", self.apply_preset),
            ("Guardar preset", self.save_preset),
            ("Importar línea CLI", self.import_cli_line),
            ("Exportar", self.export_current_tab),
            ("Previsualizar", self.preview_current_tab),
            ("Generar EPUB", self.generate_epub),
            ("Exportar OEB", self.export_oeb),
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
        widget.rowconfigure(3, weight=0)

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
        self._create_side_panel(widget, tab_widget_id)

        form = ConfigForm(
            widget,
            catalog=self.catalog,
            config=config,
            on_change=lambda option_id: self._handle_form_change(tab_widget_id, option_id),
        )
        form.grid(row=2, column=0, sticky="nsew", padx=(12, 6), pady=(0, 12))

        status = ttk.Label(widget, text="Listo", anchor="w", padding=(12, 6))
        status.grid(row=3, column=0, columnspan=2, sticky="ew")

        self.notebook.add(widget, text=config.title)
        self._config_by_tab[tab_widget_id] = config
        self._summary_labels[tab_widget_id] = summary
        self._forms[tab_widget_id] = form
        self._status_labels[tab_widget_id] = status
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

    def _create_side_panel(self, parent: ttk.Frame, tab_widget_id: str) -> None:
        panel = ttk.Frame(parent)
        panel.grid(row=2, column=1, sticky="nsew", padx=(0, 12), pady=(0, 12))
        panel.rowconfigure(0, weight=3)
        panel.rowconfigure(1, weight=2)
        panel.columnconfigure(0, weight=1)

        viewer_frame = ttk.LabelFrame(panel, text="Visor HTML")
        viewer_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        viewer_frame.columnconfigure(0, weight=1)
        viewer_frame.rowconfigure(0, weight=1)

        viewer = HtmlViewer(viewer_frame)
        viewer.grid(row=0, column=0, sticky="nsew", padx=6, pady=(6, 0))

        reload_button = ttk.Button(
            viewer_frame,
            text="Recargar",
            command=lambda tab_id=tab_widget_id: self.reload_preview(tab_id),
        )
        reload_button.grid(row=1, column=0, sticky="w", padx=6, pady=6)

        console_frame = ttk.LabelFrame(panel, text="Consola")
        console_frame.grid(row=1, column=0, sticky="nsew")
        console_frame.rowconfigure(0, weight=1)
        console_frame.columnconfigure(0, weight=1)

        console = tk.Text(console_frame, wrap="word", height=8, state="disabled")
        console.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(console_frame, orient="vertical", command=console.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        console.configure(yscrollcommand=scrollbar.set)

        self._viewer_widgets[tab_widget_id] = viewer
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
        viewer = self._viewer_widgets.get(tab_widget_id)
        if viewer is not None:
            viewer.reset()

    def _update_status(self, tab_widget_id: str, message: str) -> None:
        label = self._status_labels.get(tab_widget_id)
        if label is not None:
            label.configure(text=message)

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
            f"Primer HTML: {result.spine_first_html}",
            self._render_stream("stdout", result.stdout),
            self._render_stream("stderr", result.stderr),
        ]
        return "\n\n".join(pieces)

    def _format_run_error(self, error: Any) -> str:
        command = getattr(error, "command", None) or []
        pieces = [
            f"[ERROR] {error}",
            f"Comando: {self._format_command(command) if command else '<no ejecutado>'}",
            self._render_stream("stdout", getattr(error, "stdout", "")),
            self._render_stream("stderr", getattr(error, "stderr", "")),
        ]
        return "\n\n".join(pieces)

    def _format_conversion_success(self, result: ConversionResult) -> str:
        pieces = [
            "[OK] EPUB generado.",
            f"Comando: {self._format_command(result.command)}",
            f"Archivo: {result.target}",
            self._render_stream("stdout", result.stdout),
            self._render_stream("stderr", result.stderr),
        ]
        return "\n\n".join(pieces)

    def _ask_output_epub(self, config: TabConfiguration) -> Optional[str]:  # pragma: no cover - UI helper
        initialfile = (config.output_epub.name if config.output_epub else f"{config.title or 'salida'}.epub")
        initialdir = None
        if config.output_epub:
            initialdir = str(config.output_epub.parent)
        elif config.input_pdf:
            initialdir = str(Path(config.input_pdf).parent)
        return filedialog.asksaveasfilename(
            defaultextension=".epub",
            filetypes=[("EPUB", "*.epub"), ("Todos los archivos", "*.*")],
            initialfile=initialfile,
            initialdir=initialdir,
            title="Guardar EPUB",
            parent=self.winfo_toplevel(),
        )

    def _ask_oeb_directory(self, config: TabConfiguration) -> Optional[str]:  # pragma: no cover - UI helper
        initialdir = None
        if config.output_epub:
            initialdir = str(config.output_epub.parent)
        elif config.input_pdf:
            initialdir = str(Path(config.input_pdf).parent)
        return filedialog.askdirectory(
            mustexist=False,
            initialdir=initialdir,
            title="Selecciona directorio para exportar el OEB",
            parent=self.winfo_toplevel(),
        )

    def _count_warnings(self, *streams: str) -> int:
        total = 0
        for stream in streams:
            if not stream:
                continue
            for line in stream.splitlines():
                if "warning" in line.lower():
                    total += 1
        return total

    def _directory_size(self, path: Path) -> int:
        total = 0
        for root, _dirs, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    continue
        return total

    def _format_size(self, size: int) -> str:
        units = ["B", "KB", "MB", "GB"]
        value = float(size)
        for unit in units:
            if value < 1024 or unit == units[-1]:
                return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} {unit}"
            value /= 1024

    def _format_duration(self, seconds: float) -> str:
        if seconds < 1:
            return f"{seconds * 1000:.0f} ms"
        return f"{seconds:.2f} s"

    def preview_current_tab(self) -> None:
        tab_id = self._current_tab_id()
        if tab_id is None:
            self._error_handler("No hay pestaña seleccionada para previsualizar.")
            return

        config = self._config_by_tab[tab_id]
        self._append_console(tab_id, "Ejecutando previsualización…", clear=True)

        start = time.perf_counter()
        try:
            result = self._preview_runner(config)
        except PreviewError as exc:
            self._append_console(tab_id, self._format_run_error(exc), clear=True)
            self._error_handler(str(exc))
            self._update_status(tab_id, f"Error en previsualización: {exc}")
            return

        self._cleanup_preview_state(tab_id)
        self._preview_state[tab_id] = result
        viewer = self._viewer_widgets.get(tab_id)
        if viewer is not None:
            viewer.load(result.spine_first_html)
        self._append_console(tab_id, self._format_preview_success(result), clear=True)
        duration = time.perf_counter() - start
        warnings = self._count_warnings(result.stdout, result.stderr)
        size = self._format_size(self._directory_size(result.oeb_output))
        self._update_status(
            tab_id,
            f"Previsualización en {self._format_duration(duration)} · Warnings: {warnings} · Tamaño: {size}",
        )

    def generate_epub(self) -> None:
        tab_id = self._current_tab_id()
        if tab_id is None:
            self._error_handler("No hay pestaña seleccionada para generar el EPUB.")
            return

        config = self._config_by_tab[tab_id]
        if not config.input_pdf:
            self._error_handler("Selecciona primero un PDF de entrada para generar el EPUB.")
            return

        target = self._ask_output_epub(config)
        if not target:
            return

        target_path = Path(target)
        config.output_epub = target_path
        self._summary_labels[tab_id].configure(text=self._format_summary(config))

        self._append_console(tab_id, "Generando EPUB…")

        start = time.perf_counter()
        try:
            result = self._conversion_runner(config, target_path)
        except ConversionError as exc:
            self._append_console(tab_id, self._format_run_error(exc))
            self._error_handler(str(exc))
            self._update_status(tab_id, f"Error al generar EPUB: {exc}")
            return

        self._append_console(tab_id, self._format_conversion_success(result))
        duration = time.perf_counter() - start
        warnings = self._count_warnings(result.stdout, result.stderr)
        size = self._format_size(result.target.stat().st_size)
        self._update_status(
            tab_id,
            f"EPUB listo en {self._format_duration(duration)} · Warnings: {warnings} · Tamaño: {size}",
        )

    def export_oeb(self) -> None:
        tab_id = self._current_tab_id()
        if tab_id is None:
            self._error_handler("No hay pestaña seleccionada para exportar el OEB.")
            return

        config = self._config_by_tab[tab_id]
        if not config.input_pdf:
            self._error_handler("Selecciona primero un PDF de entrada para exportar el OEB.")
            return

        result = self._preview_state.get(tab_id)
        if result is None:
            self.preview_current_tab()
            result = self._preview_state.get(tab_id)
            if result is None:
                return

        target = self._ask_oeb_directory(config)
        if not target:
            return

        export_dir = Path(target)
        source_dir = result.oeb_output
        if export_dir.resolve() == source_dir.resolve():
            self._error_handler("El directorio destino no puede ser el mismo que el origen.")
            self._append_console(tab_id, "[ERROR] Directorio destino inválido.")
            return

        if export_dir.exists():
            if not export_dir.is_dir():
                self._error_handler("El destino seleccionado no es un directorio.")
                self._append_console(tab_id, "[ERROR] El destino seleccionado no es un directorio.")
                return
            if any(export_dir.iterdir()):
                self._error_handler("El directorio destino debe estar vacío.")
                self._append_console(tab_id, "[ERROR] El directorio destino debe estar vacío.")
                return
        else:
            export_dir.mkdir(parents=True, exist_ok=True)

        self._append_console(tab_id, f"Exportando OEB a {export_dir}…")
        start = time.perf_counter()
        try:
            shutil.copytree(source_dir, export_dir, dirs_exist_ok=True)
        except OSError as exc:
            self._append_console(tab_id, f"[ERROR] No se pudo exportar el OEB: {exc}")
            self._error_handler(f"No se pudo exportar el OEB: {exc}")
            self._update_status(tab_id, f"Error al exportar OEB: {exc}")
            return

        self._append_console(tab_id, f"[OK] OEB exportado en: {export_dir}")
        duration = time.perf_counter() - start
        size = self._format_size(self._directory_size(export_dir))
        warnings = self._count_warnings("", "")
        self._update_status(
            tab_id,
            f"OEB exportado en {self._format_duration(duration)} · Warnings: {warnings} · Tamaño: {size}",
        )

    def reload_preview(self, tab_widget_id: Optional[str] = None) -> None:
        tab_id = tab_widget_id or self._current_tab_id()
        if tab_id is None:
            self._error_handler("No hay pestaña seleccionada para recargar.")
            return

        result = self._preview_state.get(tab_id)
        viewer = self._viewer_widgets.get(tab_id)
        if not result or viewer is None:
            self._error_handler("Aún no hay previsualización disponible para recargar.")
            return

        viewer.reload()
        self._append_console(tab_id, "Recarga del visor completada.")

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

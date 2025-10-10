"""Tkinter notebook widget managing per-tab configurations."""

from __future__ import annotations

import copy
import os
import queue
import shlex
import shutil
import subprocess
import threading
import time
import textwrap
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence
from uuid import uuid4

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont

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

FONT_PREVIEW_SAMPLE = "abcABC123!? ÁÉÍÓÚ ñÑ"


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
        font_size: Optional[int] = None,
        font_family: Optional[str] = None,
        on_font_size_changed: Optional[Callable[[int], None]] = None,
        on_font_family_changed: Optional[Callable[[str], None]] = None,
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
        self._running_jobs: Dict[str, dict] = {}
        self._preview_runner = preview_runner
        self._conversion_runner = conversion_runner
        self._presets = list(presets) if presets is not None else get_presets()
        self._preset_selector = preset_selector or (lambda items: choose_preset(self, items))
        self._input_controls: Dict[str, Dict[str, Any]] = {}
        self._tab_counter = 1
        self._font_size = max(8, min(24, int(font_size or 11)))
        default_font = tkfont.nametofont("TkDefaultFont")
        self._font_family = font_family or default_font.cget("family")
        self._on_font_size_changed = on_font_size_changed
        self._on_font_family_changed = on_font_family_changed
        self._toolbar_buttons: List[tuple[ttk.Button, str]] = []

        self._build_toolbar()
        self.new_tab()

    # -- Public API -----------------------------------------------------

    def _bind_mousewheel(self, canvas: tk.Canvas, target: tk.Widget) -> None:
        """Enable mouse wheel scrolling on the provided canvas."""

        def _on_mousewheel(event: tk.Event) -> str:
            delta = event.delta
            if delta == 0:
                if getattr(event, "num", None) == 4:
                    delta = 120
                elif getattr(event, "num", None) == 5:
                    delta = -120
            if delta:
                canvas.yview_scroll(int(-delta / 120), "units")
            return "break"

        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind(sequence, _on_mousewheel, add=True)
            target.bind(sequence, _on_mousewheel, add=True)

    def _adjust_wrap(self, label: ttk.Label, width: int, *, min_wrap: int = 200, padding: int = 24) -> None:
        if width <= padding:
            return
        wrap = max(min_wrap, width - padding)
        try:
            current = int(label.cget("wraplength"))
        except (tk.TclError, ValueError, TypeError):
            current = 0
        if wrap != current:
            label.configure(wraplength=wrap)

    def _refresh_toolbar_buttons(self) -> None:
        if not self._toolbar_buttons:
            return
        total = len(self._toolbar_buttons)
        for idx in range(total):
            self.toolbar.grid_columnconfigure(idx, weight=1)
        base_font = tkfont.nametofont("TkDefaultFont")
        for button, base_text in self._toolbar_buttons:
            button.configure(text=base_text)
            width = button.winfo_width()
            if width <= 1:
                continue
            available = max(32, width - 16)
            text_width = base_font.measure(base_text)
            if text_width <= available:
                continue
            char_width = base_font.measure("M") or 1
            max_chars = max(4, available // char_width)
            wrapped = textwrap.fill(base_text, width=max_chars)
            if wrapped != button.cget("text"):
                button.configure(text=wrapped)

    def _apply_text_scaling(self) -> None:
        size = max(8, min(24, int(self._font_size)))
        for viewer in self._viewer_widgets.values():
            viewer.apply_font_scale(size)

    def _start_background_job(
        self,
        tab_id: str,
        *,
        job_name: str,
        runner: Callable[[Callable[[str, Any], None]], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None],
    ) -> bool:
        if tab_id in self._running_jobs:
            self._error_handler("Ya hay una tarea en ejecución en esta pestaña. Espera a que finalice.")
            return False

        task_queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()

        def send(kind: str, payload: Any) -> None:
            task_queue.put((kind, payload))

        def worker() -> None:
            try:
                result = runner(send)
            except Exception as exc:  # pragma: no cover - handled in UI thread
                task_queue.put(("__error__", exc))
            else:
                task_queue.put(("__result__", result))

        state = {
            "queue": task_queue,
            "thread": threading.Thread(target=worker, name=f"{job_name}-{tab_id}", daemon=True),
            "on_success": on_success,
            "on_error": on_error,
            "job_name": job_name,
        }
        self._running_jobs[tab_id] = state
        state["thread"].start()
        self.after(25, lambda: self._poll_job_queue(tab_id))
        return True

    def _poll_job_queue(self, tab_id: str) -> None:
        state = self._running_jobs.get(tab_id)
        if not state:
            return

        task_queue: queue.Queue = state["queue"]
        should_reschedule = True

        while True:
            try:
                kind, payload = task_queue.get_nowait()
            except queue.Empty:
                break

            if kind == "__result__":
                self._running_jobs.pop(tab_id, None)
                try:
                    state["on_success"](payload)
                finally:
                    should_reschedule = False
            elif kind == "__error__":
                self._running_jobs.pop(tab_id, None)
                try:
                    state["on_error"](payload)
                finally:
                    should_reschedule = False
            elif kind == "stream":
                stream_kind, text = payload
                snippet = str(text).rstrip("\n")
                if snippet:
                    prefix = "[stdout]" if stream_kind == "stdout" else "[stderr]"
                    self._append_console(tab_id, f"{prefix} {snippet}")
            elif kind == "message":
                self._append_console(tab_id, str(payload))
            elif kind == "status":
                self._update_status(tab_id, str(payload))

        if should_reschedule and tab_id in self._running_jobs:
            self.after(50, lambda: self._poll_job_queue(tab_id))

    def _make_streaming_run(
        self,
        tab_id: str,
        send: Callable[[str, Any], None],
    ) -> Callable[..., subprocess.CompletedProcess]:
        def _runner(
            command: Sequence[str],
            *,
            capture_output: bool = True,
            text: bool = True,
            check: bool = False,
            **kwargs: Any,
        ) -> subprocess.CompletedProcess:
            return self._stream_subprocess(
                tab_id,
                send,
                command,
                capture_output=capture_output,
                text=text,
                check=check,
                **kwargs,
            )

        return _runner

    def _stream_subprocess(
        self,
        tab_id: str,
        send: Callable[[str, Any], None],
        command: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        **kwargs: Any,
    ) -> subprocess.CompletedProcess:
        if not capture_output:
            return subprocess.run(
                command,
                capture_output=capture_output,
                text=text,
                check=check,
                **kwargs,
            )

        if "stdout" in kwargs or "stderr" in kwargs:
            raise ValueError("streaming runner no soporta redirecciones personalizadas de stdout/stderr")

        popen_kwargs = dict(kwargs)
        popen_kwargs.setdefault("bufsize", 1 if text else 0)
        popen_kwargs.setdefault("text", text)
        popen_kwargs.setdefault("universal_newlines", text)
        popen_kwargs["stdout"] = subprocess.PIPE
        popen_kwargs["stderr"] = subprocess.PIPE

        process = subprocess.Popen(command, **popen_kwargs)
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def _pump(stream: Optional[Any], kind: str, accumulator: list[str]) -> None:
            if stream is None:
                return
            for line in iter(stream.readline, ""):
                accumulator.append(line)
                send("stream", (kind, line))
            stream.close()

        stdout_thread = threading.Thread(
            target=_pump,
            args=(process.stdout, "stdout", stdout_chunks),
            name=f"{tab_id}-stdout",
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_pump,
            args=(process.stderr, "stderr", stderr_chunks),
            name=f"{tab_id}-stderr",
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        returncode = process.wait()
        stdout_thread.join()
        stderr_thread.join()

        stdout = "".join(stdout_chunks)
        stderr = "".join(stderr_chunks)
        completed = subprocess.CompletedProcess(list(command), returncode, stdout, stderr)
        if check and returncode != 0:
            raise subprocess.CalledProcessError(returncode, list(command), stdout, stderr)
        return completed

    def new_tab(self) -> Optional[str]:
        """Create a brand new configuration tab."""
        tab_id = self._generate_tab_id()
        title = self._generate_title()
        config = TabConfiguration(tab_id=tab_id, title=title)
        return self._add_tab(config)

    def has_running_job(self, tab_id: Optional[str] = None) -> bool:
        """Return True when there is an active background job."""
        if tab_id is not None:
            return tab_id in self._running_jobs
        return bool(self._running_jobs)

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
            ("Tamaño letra", self.change_font_size),
            ("Fuente base", self.change_font_family),
        ]

        for idx, (label, command) in enumerate(buttons):
            button = ttk.Button(self.toolbar, text=label, command=command)
            button.grid(row=0, column=idx, padx=4, pady=4, sticky="nsew")
            button.configure(takefocus=True)
            self.toolbar.grid_columnconfigure(idx, weight=1)
            self._toolbar_buttons.append((button, label))

    def _add_tab(self, config: TabConfiguration) -> str:
        widget = ttk.Frame(self.notebook)
        widget.columnconfigure(0, weight=1, uniform="pane")
        widget.columnconfigure(1, weight=1, uniform="pane")
        widget.rowconfigure(0, weight=1)

        left = ttk.Frame(widget)
        left.grid(row=0, column=0, sticky="nsew", padx=(12, 6), pady=12)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)

        right = ttk.Frame(widget)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=12)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=3)
        right.rowconfigure(1, weight=1)

        tab_widget_id = str(widget)

        summary = ttk.Label(
            left,
            text=self._format_summary(config),
            padding=12,
            justify="left",
            anchor="nw",
            wraplength=600,
        )
        summary.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        summary.bind(
            "<Configure>",
            lambda event, label=summary: self._adjust_wrap(label, event.width, min_wrap=260),
            add="+",
        )

        self._create_input_controls(left, config, tab_widget_id)
        self._create_side_panel(right, tab_widget_id)

        form_container = ttk.Frame(left)
        form_container.grid(row=2, column=0, sticky="nsew")
        form_container.columnconfigure(0, weight=1)
        form_container.rowconfigure(0, weight=1)

        canvas = tk.Canvas(form_container, highlightthickness=0, borderwidth=0)
        canvas.grid(row=0, column=0, sticky="nsew")
        canvas.configure(yscrollincrement=20)

        scrollbar = ttk.Scrollbar(form_container, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        canvas.configure(yscrollcommand=scrollbar.set)

        inner = ttk.Frame(canvas)
        inner.columnconfigure(0, weight=1)
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(event: tk.Event) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event: tk.Event) -> None:
            canvas.itemconfigure(window_id, width=event.width)

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        self._bind_mousewheel(canvas, inner)

        form = ConfigForm(
            inner,
            catalog=self.catalog,
            config=config,
            on_change=lambda option_id: self._handle_form_change(tab_widget_id, option_id),
        )
        form.grid(row=0, column=0, sticky="nsew")

        status = ttk.Label(left, text="Listo", anchor="w", padding=(12, 6), wraplength=400, justify="left")
        status.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        status.bind(
            "<Configure>",
            lambda event, label=status: self._adjust_wrap(label, event.width, min_wrap=200),
            add="+",
        )

        self.notebook.add(widget, text=config.title)
        self._config_by_tab[tab_widget_id] = config
        self._summary_labels[tab_widget_id] = summary
        self._forms[tab_widget_id] = form
        self._status_labels[tab_widget_id] = status
        self._apply_text_scaling()
        self.notebook.select(widget)
        self.after_idle(self.refresh_layouts)
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
        frame.grid(row=1, column=0, sticky="ew", pady=(0, 12))
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
            text="Seleccionar...",
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
        parent.rowconfigure(0, weight=3)
        parent.rowconfigure(1, weight=1)
        parent.columnconfigure(0, weight=1)

        viewer_frame = ttk.LabelFrame(parent, text="Visor HTML")
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

        console_frame = ttk.LabelFrame(parent, text="Consola")
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
        viewer.apply_font_scale(self._font_size)

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

    def _format_preview_success(self, result: PreviewResult, *, include_streams: bool = True) -> str:
        pieces = [
            "[OK] Previsualización completada.",
            f"Comando: {self._format_command(result.command)}",
            f"OEB generado en: {result.oeb_output}",
            f"Primer HTML: {result.spine_first_html}",
        ]
        if result.skipped_options:
            skipped = ", ".join(sorted(result.skipped_options))
            pieces.append(f"Opciones omitidas (no soportadas por ebook-convert): {skipped}")
        if include_streams:
            pieces.extend(
                [
                    self._render_stream("stdout", result.stdout),
                    self._render_stream("stderr", result.stderr),
                ]
            )
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

    def _format_conversion_success(self, result: ConversionResult, *, include_streams: bool = True) -> str:
        pieces = [
            "[OK] EPUB generado.",
            f"Comando: {self._format_command(result.command)}",
            f"Archivo: {result.target}",
        ]
        if result.skipped_options:
            skipped = ", ".join(sorted(result.skipped_options))
            pieces.append(f"Opciones omitidas (no soportadas por ebook-convert): {skipped}")
        if include_streams:
            pieces.extend(
                [
                    self._render_stream("stdout", result.stdout),
                    self._render_stream("stderr", result.stderr),
                ]
            )
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

    def preview_current_tab(self, on_complete: Optional[Callable[[PreviewResult], None]] = None) -> None:
        tab_id = self._current_tab_id()
        if tab_id is None:
            self._error_handler("No hay pestaña seleccionada para previsualizar.")
            return
        if tab_id in self._running_jobs:
            self._error_handler("Ya hay una tarea en ejecución en esta pestaña. Espera a que finalice.")
            return

        config = self._config_by_tab[tab_id]
        if not config.input_pdf:
            error = PreviewError(
                "Selecciona primero un PDF de entrada para previsualizar.",
                command=[],
                stdout="",
                stderr="",
                returncode=None,
            )
            self._append_console(tab_id, self._format_run_error(error), clear=True)
            self._error_handler(str(error))
            self._update_status(tab_id, f"Error en previsualización: {error}")
            return

        self._append_console(tab_id, "Ejecutando previsualización…", clear=True)
        self._update_status(tab_id, "Previsualización en curso…")

        start = time.perf_counter()
        streaming_enabled = self._preview_runner is None

        def runner(send: Callable[[str, Any], None]) -> PreviewResult:
            if self._preview_runner is None:
                return run_preview(
                    config,
                    catalog=self.catalog,
                    run=self._make_streaming_run(tab_id, send),
                )
            return self._preview_runner(config)

        def on_success(result: PreviewResult) -> None:
            self._cleanup_preview_state(tab_id)
            self._preview_state[tab_id] = result
            viewer = self._viewer_widgets.get(tab_id)
            if viewer is not None:
                viewer.load(result.spine_first_html)
            include_streams = not streaming_enabled
            summary = self._format_preview_success(result, include_streams=include_streams)
            if streaming_enabled:
                if summary:
                    self._append_console(tab_id, "")
                    self._append_console(tab_id, summary)
            else:
                self._append_console(tab_id, summary, clear=True)
            duration = time.perf_counter() - start
            warnings = self._count_warnings(result.stdout, result.stderr)
            size = self._format_size(self._directory_size(result.oeb_output))
            status = f"Previsualización en {self._format_duration(duration)} · Warnings: {warnings} · Tamaño: {size}"
            if result.skipped_options:
                status += f" · Opciones omitidas: {len(result.skipped_options)}"
            self._update_status(tab_id, status)
            if on_complete is not None:
                on_complete(result)

        def on_error(exc: Exception) -> None:
            if isinstance(exc, PreviewError):
                self._append_console(tab_id, self._format_run_error(exc))
                self._error_handler(str(exc))
                self._update_status(tab_id, f"Error en previsualización: {exc}")
            else:  # pragma: no cover - defensive path
                self._append_console(tab_id, f"[ERROR] {exc}")
                self._error_handler(str(exc))
                self._update_status(tab_id, f"Error en previsualización: {exc}")

        self._start_background_job(
            tab_id,
            job_name="preview",
            runner=runner,
            on_success=on_success,
            on_error=on_error,
        )

    def generate_epub(self) -> None:
        tab_id = self._current_tab_id()
        if tab_id is None:
            self._error_handler("No hay pestaña seleccionada para generar el EPUB.")
            return

        if tab_id in self._running_jobs:
            self._error_handler("Ya hay una tarea en ejecución en esta pestaña. Espera a que finalice.")
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

        self._append_console(tab_id, "Generando EPUB…", clear=True)
        self._update_status(tab_id, "Generación en curso…")

        start = time.perf_counter()
        streaming_enabled = self._conversion_runner is None

        def runner(send: Callable[[str, Any], None]) -> ConversionResult:
            if self._conversion_runner is None:
                return run_epub(
                    config,
                    target_path,
                    catalog=self.catalog,
                    run=self._make_streaming_run(tab_id, send),
                )
            return self._conversion_runner(config, target_path)

        def on_success(result: ConversionResult) -> None:
            include_streams = not streaming_enabled
            summary = self._format_conversion_success(result, include_streams=include_streams)
            if streaming_enabled:
                if summary:
                    self._append_console(tab_id, "")
                    self._append_console(tab_id, summary)
            else:
                self._append_console(tab_id, summary, clear=True)
            duration = time.perf_counter() - start
            warnings = self._count_warnings(result.stdout, result.stderr)
            size = self._format_size(result.target.stat().st_size)
            status = f"EPUB listo en {self._format_duration(duration)} · Warnings: {warnings} · Tamaño: {size}"
            if result.skipped_options:
                status += f" · Opciones omitidas: {len(result.skipped_options)}"
            self._update_status(tab_id, status)

        def on_error(exc: Exception) -> None:
            if isinstance(exc, ConversionError):
                self._append_console(tab_id, self._format_run_error(exc))
                self._error_handler(str(exc))
                self._update_status(tab_id, f"Error al generar EPUB: {exc}")
            else:  # pragma: no cover - defensive fallback
                self._append_console(tab_id, f"[ERROR] {exc}")
                self._error_handler(str(exc))
                self._update_status(tab_id, f"Error al generar EPUB: {exc}")

        self._start_background_job(
            tab_id,
            job_name="conversion",
            runner=runner,
            on_success=on_success,
            on_error=on_error,
        )

    def export_oeb(self) -> None:
        tab_id = self._current_tab_id()
        if tab_id is None:
            self._error_handler("No hay pestaña seleccionada para exportar el OEB.")
            return

        if self.has_running_job(tab_id):
            self._error_handler("Espera a que termine la tarea en curso antes de exportar el OEB.")
            self._append_console(tab_id, "[ERROR] Hay una tarea en ejecución que impide exportar el OEB.")
            return

        config = self._config_by_tab[tab_id]
        if not config.input_pdf:
            self._error_handler("Selecciona primero un PDF de entrada para exportar el OEB.")
            return

        result = self._preview_state.get(tab_id)
        if result is None:
            self._append_console(
                tab_id,
                "Generando previsualización antes de exportar el OEB…",
            )
            self._update_status(tab_id, "Previsualización requerida para exportar OEB…")

            def _resume_export(_: PreviewResult) -> None:
                self.export_oeb()

            self.preview_current_tab(on_complete=_resume_export)
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

    def refresh_layouts(self) -> None:
        self.update_idletasks()
        self._refresh_toolbar_buttons()
        self._apply_text_scaling()
        for form in self._forms.values():
            form.update_responsive_layout()
        for label in self._summary_labels.values():
            width = label.winfo_width()
            if width > 0:
                self._adjust_wrap(label, width, min_wrap=260)
        for label in self._status_labels.values():
            width = label.winfo_width()
            if width > 0:
                self._adjust_wrap(label, width, min_wrap=200)

    def change_font_size(self) -> None:
        current = self._font_size
        size = simpledialog.askinteger(
            "Tamaño de letra",
            "Selecciona el tamaño base de la fuente (8-24 pt):",
            initialvalue=current,
            minvalue=8,
            maxvalue=24,
            parent=self.winfo_toplevel(),
        )
        if size is None or int(size) == current:
            return

        size = max(8, min(24, int(size)))
        previous = self._font_size
        self._font_size = size
        callback = self._on_font_size_changed
        if callback is not None:
            try:
                callback(size)
            except Exception as exc:
                self._font_size = previous
                self._error_handler(str(exc))
                return
        else:
            self.refresh_layouts()
        tab_id = self._current_tab_id()
        if tab_id:
            self._update_status(tab_id, f"Tamaño de letra actualizado a {size} pt")

    def change_font_family(self) -> None:
        try:
            families = sorted(set(tkfont.families(self)))
        except tk.TclError as exc:
            self._error_handler(f"No se pudieron obtener las fuentes: {exc}")
            return
        if not families:
            self._error_handler("No se encontraron fuentes instaladas.")
            return

        current_font = self._font_family or tkfont.nametofont("TkDefaultFont").cget("family")
        selection: Dict[str, Optional[str]] = {"family": None}

        dialog = tk.Toplevel(self)
        dialog.title("Seleccionar fuente base")
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()
        dialog.resizable(False, False)

        content = ttk.Frame(dialog, padding=12)
        content.grid(row=0, column=0, sticky="nsew")

        ttk.Label(content, text="Elige una fuente:").grid(row=0, column=0, sticky="w")

        list_frame = ttk.Frame(content)
        list_frame.grid(row=1, column=0, sticky="nsew", pady=(6, 12))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        listbox = tk.Listbox(list_frame, height=12, exportselection=False)
        listbox.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        listbox.configure(yscrollcommand=scrollbar.set)

        for family in families:
            listbox.insert(tk.END, family)

        listbox.focus_set()

        preview_font = tkfont.Font(family=current_font, size=max(12, self._font_size))
        preview_label = ttk.Label(content, text=FONT_PREVIEW_SAMPLE, font=preview_font, padding=(0, 8))
        preview_label.grid(row=2, column=0, sticky="ew")

        def update_preview(*_args: Any) -> None:
            selection_indices = listbox.curselection()
            if not selection_indices:
                return
            family = families[selection_indices[0]]
            try:
                preview_font.configure(family=family)
            except tk.TclError:
                return

        def confirm(event: Optional[tk.Event] = None) -> None:
            selection_indices = listbox.curselection()
            if not selection_indices:
                return
            family = families[selection_indices[0]]
            selection["family"] = family
            dialog.destroy()

        def cancel() -> None:
            selection["family"] = None
            dialog.destroy()

        button_bar = ttk.Frame(content)
        button_bar.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        button_bar.columnconfigure(0, weight=1)
        button_bar.columnconfigure(1, weight=1)

        ok_button = ttk.Button(button_bar, text="Aceptar", command=confirm)
        ok_button.grid(row=0, column=0, sticky="ew", padx=(0, 6))
        cancel_button = ttk.Button(button_bar, text="Cancelar", command=cancel)
        cancel_button.grid(row=0, column=1, sticky="ew")

        listbox.bind("<<ListboxSelect>>", update_preview)
        listbox.bind("<Double-Button-1>", confirm)
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.bind("<Return>", confirm)

        try:
            index = families.index(current_font)
        except ValueError:
            index = 0 if families else None
        if index is not None and families:
            listbox.selection_set(index)
            listbox.see(index)
            update_preview()

        dialog.wait_window()

        chosen = selection.get("family")
        if not chosen or chosen == self._font_family:
            return

        previous = self._font_family
        self._font_family = chosen
        callback = self._on_font_family_changed
        if callback is not None:
            try:
                callback(chosen)
            except Exception as exc:
                self._font_family = previous
                self._error_handler(str(exc))
                return
        else:
            self.refresh_layouts()

        tab_id = self._current_tab_id()
        if tab_id:
            self._update_status(tab_id, f"Fuente base: {chosen}")

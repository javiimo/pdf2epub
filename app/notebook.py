"""Tkinter notebook widget managing per-tab configurations."""

from __future__ import annotations

import copy
import inspect
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
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, cast
from uuid import uuid4

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont

from app.calibre_update import show_calibre_update_dialog
from app.forms import ConfigForm
from app.html_viewer import HtmlViewer
from app.preset_dialog import choose_presets
from core.configuration import ConfigurationError, TabConfiguration, save_configuration
from core.options.catalog import Catalog, get_catalog
from core.parser import SpineItem
from core.presets import Preset, PresetMergeReport, apply_presets, get_presets
from core.runner.cli_parser import (
    CliParseError,
    CliParseResult,
    parse_cli_commands,
)
from core.runner import cli_support
from core.runner.epub import (
    ConversionError,
    ConversionResult,
    run_epub,
    package_epub_from_oeb,
)
from core.runner.preview import PreviewError, PreviewResult, run_preview
from core.runner.pdf_preprocessor import options_from_extras

CliPrompt = Callable[[], Optional[str]]
ExportPrompt = Callable[[TabConfiguration], Optional[str]]
ErrorHandler = Callable[[str], None]

FONT_PREVIEW_SAMPLE = "abcABC123!? ÁÉÍÓÚ ñÑ"

PreviewRunnerCallable = Callable[[TabConfiguration, threading.Event], PreviewResult]
ConversionRunnerCallable = Callable[[TabConfiguration, Path, threading.Event], ConversionResult]


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
        preview_runner: Optional[Callable[..., PreviewResult]] = None,
        conversion_runner: Optional[Callable[..., ConversionResult]] = None,
        presets: Optional[Iterable[Preset]] = None,
        preset_selector: Optional[Callable[[List[Preset]], List[Preset]]] = None,
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
        self._spine_controls: Dict[str, Dict[str, Any]] = {}
        self._preprocess_controls: Dict[str, Dict[str, Any]] = {}
        self._preview_state: Dict[str, PreviewResult] = {}
        self._running_jobs: Dict[str, dict] = {}
        self._preview_runner = self._wrap_preview_runner(preview_runner)
        self._conversion_runner = self._wrap_conversion_runner(conversion_runner)
        self._presets = list(presets) if presets is not None else get_presets()
        self._preset_selector = preset_selector or (lambda items: choose_presets(self, items))
        self._input_controls: Dict[str, Dict[str, Any]] = {}
        self._scroll_areas: list[tuple[tk.Canvas, tk.Widget]] = []
        self._global_mousewheel_bound = False
        self._tab_counter = 1
        self._font_size = max(8, min(24, int(font_size or 11)))
        default_font = tkfont.nametofont("TkDefaultFont")
        self._font_family = font_family or default_font.cget("family")
        self._on_font_size_changed = on_font_size_changed
        self._on_font_family_changed = on_font_family_changed
        self._toolbar_buttons: List[tuple[ttk.Button, str]] = []
        self._cancel_button: Optional[ttk.Button] = None

        self._build_toolbar()
        self.notebook.bind("<<NotebookTabChanged>>", lambda _event: self._update_cancel_button_state())
        self.new_tab()
        self._update_cancel_button_state()

    @staticmethod
    def _runner_accepts_argument(runner: Callable[..., Any], position: int) -> bool:
        try:
            signature = inspect.signature(runner)
        except (TypeError, ValueError):
            return False
        params = list(signature.parameters.values())
        if any(param.kind == inspect.Parameter.VAR_POSITIONAL for param in params[position:]):
            return True
        if len(params) > position:
            candidate = params[position]
            if candidate.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            ):
                return True
        return False

    @staticmethod
    def _wrap_preview_runner(
        runner: Optional[Callable[..., PreviewResult]]
    ) -> Optional[PreviewRunnerCallable]:
        if runner is None:
            return None
        if ConfigNotebook._runner_accepts_argument(runner, 1):
            return cast(PreviewRunnerCallable, runner)

        def _wrapped(config: TabConfiguration, _: threading.Event) -> PreviewResult:
            return runner(config)  # type: ignore[misc]

        return _wrapped

    @staticmethod
    def _wrap_conversion_runner(
        runner: Optional[Callable[..., ConversionResult]]
    ) -> Optional[ConversionRunnerCallable]:
        if runner is None:
            return None
        if ConfigNotebook._runner_accepts_argument(runner, 2):
            return cast(ConversionRunnerCallable, runner)

        def _wrapped(config: TabConfiguration, target: Path, _: threading.Event) -> ConversionResult:
            return runner(config, target)  # type: ignore[misc]

        return _wrapped

    # -- Public API -----------------------------------------------------

    def _bind_mousewheel(self, canvas: tk.Canvas, target: tk.Widget) -> None:
        """Enable mouse wheel scrolling on the provided canvas."""

        self._scroll_areas.append((canvas, target))
        self._ensure_global_mousewheel_binding()

        def _remove_area(_: tk.Event) -> None:
            try:
                self._scroll_areas.remove((canvas, target))
            except ValueError:
                pass

        canvas.bind("<Destroy>", _remove_area, add=True)
        target.bind("<Destroy>", _remove_area, add=True)

    def _ensure_global_mousewheel_binding(self) -> None:
        if self._global_mousewheel_bound:
            return
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.bind_all(sequence, self._handle_global_mousewheel, add=True)
        self._global_mousewheel_bound = True

    def _handle_global_mousewheel(self, event: tk.Event) -> str | None:
        canvas = self._canvas_for_event(event)
        if canvas is None:
            return None

        delta = getattr(event, "delta", 0)
        if delta == 0:
            if getattr(event, "num", None) == 4:
                delta = 120
            elif getattr(event, "num", None) == 5:
                delta = -120
        if delta:
            canvas.yview_scroll(int(-delta / 120), "units")
            return "break"
        return None

    def _canvas_for_event(self, event: tk.Event) -> Optional[tk.Canvas]:
        widget = getattr(event, "widget", None)
        candidate = self._canvas_for_widget(widget)
        if candidate is not None:
            return candidate

        if hasattr(event, "x_root") and hasattr(event, "y_root"):
            try:
                widget = self.winfo_containing(int(event.x_root), int(event.y_root))
            except (tk.TclError, KeyError):
                widget = None
            if widget is not None:
                return self._canvas_for_widget(widget)

        return None

    def _canvas_for_widget(self, widget: tk.Widget | None) -> Optional[tk.Canvas]:
        if widget is None:
            return None
        for canvas, target in self._scroll_areas:
            if self._is_descendant(widget, target) or self._is_descendant(widget, canvas):
                return canvas
        return None

    @staticmethod
    def _is_descendant(widget: tk.Widget | None, ancestor: tk.Widget) -> bool:
        current = widget
        while current is not None:
            if current is ancestor:
                return True
            current = getattr(current, "master", None)
        return False

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

    def _update_cancel_button_state(self) -> None:
        if self._cancel_button is None:
            return
        tab_id = self._current_tab_id()
        running = tab_id is not None and self.has_running_job(tab_id)
        self._cancel_button.configure(state=tk.NORMAL if running else tk.DISABLED)

    def _apply_text_scaling(self) -> None:
        size = max(8, min(24, int(self._font_size)))
        for viewer in self._viewer_widgets.values():
            viewer.apply_font_scale(size)

    def _start_background_job(
        self,
        tab_id: str,
        *,
        job_name: str,
        runner: Callable[[Callable[[str, Any], None], threading.Event], Any],
        on_success: Callable[[Any], None],
        on_error: Callable[[Exception], None],
    ) -> bool:
        if tab_id in self._running_jobs:
            self._error_handler("Ya hay una tarea en ejecución en esta pestaña. Espera a que finalice.")
            return False

        task_queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        cancel_event = threading.Event()

        def send(kind: str, payload: Any) -> None:
            task_queue.put((kind, payload))

        def worker() -> None:
            try:
                result = runner(send, cancel_event)
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
            "cancel_event": cancel_event,
            "processes": [],
        }
        self._running_jobs[tab_id] = state
        setattr(send, "cancel_event", cancel_event)
        state["thread"].start()
        self.after(25, lambda: self._poll_job_queue(tab_id))
        self._update_cancel_button_state()
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
                cancelled = state["cancel_event"].is_set()
                self._running_jobs.pop(tab_id, None)
                try:
                    if cancelled:
                        self._handle_job_cancelled(tab_id)
                    else:
                        state["on_success"](payload)
                finally:
                    should_reschedule = False
            elif kind == "__error__":
                cancelled = state["cancel_event"].is_set()
                self._running_jobs.pop(tab_id, None)
                try:
                    if cancelled:
                        self._handle_job_cancelled(tab_id)
                    else:
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

        if tab_id not in self._running_jobs:
            self._update_cancel_button_state()
        elif should_reschedule:
            self.after(50, lambda: self._poll_job_queue(tab_id))
        else:
            self._update_cancel_button_state()

    def _handle_job_cancelled(self, tab_id: str) -> None:
        self._append_console(tab_id, "[INFO] Tarea cancelada por el usuario.")
        self._update_status(tab_id, "Tarea cancelada por el usuario.")

    def _register_job_process(self, tab_id: str, process: subprocess.Popen[Any]) -> None:
        state = self._running_jobs.get(tab_id)
        if not state:
            return
        processes: List[subprocess.Popen[Any]] = state.setdefault("processes", [])
        processes.append(process)
        cancel_event: threading.Event = state["cancel_event"]
        if cancel_event.is_set():
            self._terminate_process(process)

    def _unregister_job_process(self, tab_id: str, process: subprocess.Popen[Any]) -> None:
        state = self._running_jobs.get(tab_id)
        if not state:
            return
        processes: List[subprocess.Popen[Any]] = state.get("processes", [])
        if process in processes:
            processes.remove(process)

    def _terminate_process(self, process: subprocess.Popen[Any]) -> None:
        if process.poll() is not None:
            return
        try:
            process.terminate()
        except OSError:
            return
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except OSError:
                pass
    def _make_streaming_run(
        self,
        tab_id: str,
        send: Callable[[str, Any], None],
        cancel_event: threading.Event,
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
                cancel_event,
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
        cancel_event: threading.Event,
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

        self._register_job_process(tab_id, process)
        try:
            returncode = process.wait()
            stdout_thread.join()
            stderr_thread.join()

            stdout = "".join(stdout_chunks)
            stderr = "".join(stderr_chunks)
            completed = subprocess.CompletedProcess(list(command), returncode, stdout, stderr)
            if check and returncode != 0:
                raise subprocess.CalledProcessError(returncode, list(command), stdout, stderr)
            return completed
        finally:
            self._unregister_job_process(tab_id, process)

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

    def cancel_current_job(self) -> None:
        tab_id = self._current_tab_id()
        if tab_id is None:
            self._error_handler("No hay pestaña seleccionada para cancelar.")
            return
        state = self._running_jobs.get(tab_id)
        if not state:
            self._append_console(tab_id, "[INFO] No hay tarea en ejecución que cancelar.")
            self._update_status(tab_id, "No hay tarea en ejecución que cancelar.")
            self._update_cancel_button_state()
            return
        cancel_event: threading.Event = state["cancel_event"]
        if cancel_event.is_set():
            return
        cancel_event.set()
        self._append_console(tab_id, "[INFO] Cancelando tarea en curso…")
        self._update_status(tab_id, "Cancelando tarea en curso…")
        processes: List[subprocess.Popen[Any]] = list(state.get("processes", []))
        for process in processes:
            self._terminate_process(process)
        self._update_cancel_button_state()

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

    def delete_current_tab(self) -> Optional[str]:
        tab_id = self._current_tab_id()
        if tab_id is None:
            self._error_handler("No hay pestaña seleccionada para eliminar.")
            return None

        if self.has_running_job(tab_id):
            self._error_handler("No se puede eliminar una configuración con una tarea en ejecución.")
            return None

        tabs = list(self.notebook.tabs())
        if len(tabs) <= 1:
            self._remove_tab(tab_id)
            new_id = self.new_tab()
            self._update_cancel_button_state()
            return new_id

        index = tabs.index(tab_id)
        self._remove_tab(tab_id)

        remaining = list(self.notebook.tabs())
        if remaining:
            next_index = min(index, len(remaining) - 1)
            self.notebook.select(remaining[next_index])

        self._update_cancel_button_state()
        return self._current_tab_id()

    def show_calibre_update(self) -> None:
        show_calibre_update_dialog(self.winfo_toplevel())

    def import_cli_line(self) -> Optional[str]:
        tab_id = self._current_tab_id()
        if tab_id is None:
            self._error_handler("No hay pestaña seleccionada para importar opciones.")
            return None

        line = self._cli_prompt()
        if not line:
            return None

        try:
            results = parse_cli_commands(line, self.catalog)
        except CliParseError as exc:
            self._error_handler(str(exc))
            return None

        if not results:
            self._error_handler("No se reconoció ningún comando ebook-convert.")
            return None

        if len(results) > 1:
            self._append_console(tab_id, "[INFO] Se detectaron múltiples comandos; se usará el primero.")

        result = results[0]
        config = self._config_by_tab[tab_id]

        options = dict(result.options)
        flag_by_option = dict(result.flag_by_option)
        unknown_flags = list(result.unknown_flags)

        unsupported_flags: List[str] = []
        supported_flags = cli_support.get_supported_flags()
        if supported_flags is not None:
            for option_id, flag in list(flag_by_option.items()):
                metadata = self.catalog.option_by_id(option_id)
                candidates = [flag]
                if metadata is not None:
                    if metadata.cli not in candidates:
                        candidates.append(metadata.cli)
                    for alias in metadata.aliases:
                        if alias not in candidates:
                            candidates.append(alias)
                if not any(candidate in supported_flags for candidate in candidates):
                    options.pop(option_id, None)
                    flag_by_option.pop(option_id, None)
                    unsupported_flags.append(flag)

        config.options.clear()
        config.options.update(options)

        self._forms[tab_id].sync_from_config()
        self._summary_labels[tab_id].configure(text=self._format_summary(config))

        status_parts = ["Opciones importadas desde CLI"]
        self._append_console(tab_id, "[OK] Opciones importadas desde CLI.")

        if unknown_flags:
            summary = ", ".join(sorted(unknown_flags))
            self._append_console(
                tab_id,
                f"[WARN] Se ignoraron flags desconocidas: {summary}",
            )
            status_parts.append("Flags desconocidas omitidas")

        if unsupported_flags:
            summary = ", ".join(sorted(unsupported_flags))
            self._append_console(
                tab_id,
                f"[WARN] El binario ebook-convert no soporta: {summary}",
            )
            status_parts.append("Flags no soportadas omitidas")

        self._update_status(tab_id, " · ".join(status_parts))

        return tab_id

    def apply_preset(self) -> None:
        tab_id = self._current_tab_id()
        if tab_id is None:
            self._error_handler("No hay pestaña seleccionada para aplicar un preset.")
            return

        if not self._presets:
            self._error_handler("No hay presets configurados.")
            return

        selection = self._preset_selector(self._presets)
        if not selection:
            return

        config = self._config_by_tab[tab_id]
        report: PresetMergeReport = apply_presets(config.options, selection)
        self._forms[tab_id].sync_from_config()
        self._summary_labels[tab_id].configure(text=self._format_summary(config))
        # Apply post-process extras from presets with 'pp.' namespace in order
        if report.applied:
            for preset in report.applied:
                for key, value in (preset.options or {}).items():
                    if not isinstance(key, str):
                        continue
                    if key.startswith("pre."):
                        try:
                            payload = str(key[4:])
                        except Exception:
                            continue
                        if payload == "device":
                            config.extras["preproc.device"] = str(value or "cpu").lower()
                        elif payload == "convert-math":
                            config.extras["preproc.convert_math"] = bool(value)
                        elif payload == "convert-inline":
                            config.extras["preproc.convert_inline"] = bool(value)
                        elif payload == "convert-tables":
                            config.extras["preproc.convert_tables"] = bool(value)
                        elif payload == "min-area":
                            try:
                                config.extras["preproc.min_area_px"] = max(0, int(value))
                            except Exception:
                                pass
                        elif payload == "margin":
                            try:
                                config.extras["preproc.margin_pts"] = max(0.0, float(value))
                            except Exception:
                                pass
                        elif payload == "pages":
                            config.extras["preproc.pages"] = str(value or "")
                        elif payload == "enabled":
                            config.extras["preproc.enabled"] = bool(value)
                        continue
                    if key.startswith("pp."):
                        continue
        self._sync_preprocess_controls(tab_id)
        applied_names = ", ".join(preset.name for preset in report.applied) or "Ninguno"
        self._append_console(tab_id, f"[OK] Presets aplicados: {applied_names}")

        if report.conflicts:
            for conflict in report.conflicts:
                self._append_console(
                    tab_id,
                    "[WARN] "
                    f"{conflict.option_id}: {conflict.overridden_preset} → {conflict.winning_preset} "
                    f"({conflict.winning_value})",
                )

        if report.notes:
            for note in report.notes:
                self._append_console(tab_id, f"[INFO] {note}")

        status_parts = [f"Presets aplicados: {applied_names}"]
        if report.conflicts:
            summary = ", ".join(
                f"{conflict.option_id}→{conflict.winning_preset}" for conflict in report.conflicts
            )
            status_parts.append(f"Conflictos: {summary}")
        if report.notes:
            status_parts.append("Revisar avisos en la consola")

        self._update_status(tab_id, " · ".join(status_parts))

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

        # Include preprocesado extras into preset options with 'pre.' namespace
        pre_options = {
            **copy.deepcopy(config.options),
            "pre.enabled": bool(config.extras.get("preproc.enabled", True)),
            "pre.device": str(config.extras.get("preproc.device", "gpu")),
            "pre.convert-math": bool(config.extras.get("preproc.convert_math", True)),
            "pre.convert-inline": bool(config.extras.get("preproc.convert_inline", True)),
            "pre.convert-tables": bool(config.extras.get("preproc.convert_tables", True)),
            "pre.min-area": int(config.extras.get("preproc.min_area_px", 150) or 150),
            "pre.margin": float(config.extras.get("preproc.margin_pts", 1.0) or 1.0),
            "pre.pages": str(config.extras.get("preproc.pages", "")),
        }
        preset = Preset(
            id=f"custom-{uuid4().hex}",
            name=name.strip(),
            description=f"Preset personalizado basado en {config.title}",
            layer="custom",
            options=pre_options,
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
            ("Eliminar", self.delete_current_tab),
            ("Presets", self.apply_preset),
            ("Guardar preset", self.save_preset),
            ("Importar línea CLI", self.import_cli_line),
            ("Exportar", self.export_current_tab),
            ("Previsualizar", self.preview_current_tab),
            ("Cancelar tarea", self.cancel_current_job),
            ("Generar EPUB", self.generate_epub),
            ("Exportar OEB", self.export_oeb),
            ("Tamaño letra", self.change_font_size),
            ("Fuente base", self.change_font_family),
            ("Actualizar Calibre", self.show_calibre_update),
        ]

        for idx, (label, command) in enumerate(buttons):
            button = ttk.Button(self.toolbar, text=label, command=command)
            button.grid(row=0, column=idx, padx=4, pady=4, sticky="nsew")
            button.configure(takefocus=True)
            self.toolbar.grid_columnconfigure(idx, weight=1)
            self._toolbar_buttons.append((button, label))
            if label == "Cancelar tarea":
                self._cancel_button = button
                button.configure(state=tk.DISABLED)

    def _add_tab(self, config: TabConfiguration) -> str:
        widget = ttk.Frame(self.notebook)
        widget.columnconfigure(0, weight=1, uniform="pane")
        widget.columnconfigure(1, weight=1, uniform="pane")
        widget.rowconfigure(0, weight=1)

        left = ttk.Frame(widget)
        left.grid(row=0, column=0, sticky="nsew", padx=(12, 6), pady=12)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(2, weight=1)  # Make preprocessing frame expandable
        left.rowconfigure(3, weight=2)  # Make form frame more expandable

        right = ttk.Frame(widget)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 12), pady=12)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=3)
        right.rowconfigure(1, weight=1)

        tab_widget_id = str(widget)

        self._ensure_preprocess_defaults(config)

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
        self._create_side_panel(right, tab_widget_id, config)
        self._create_preprocess_controls(left, tab_widget_id, config)

        form_container = ttk.Frame(left)
        form_container.grid(row=3, column=0, sticky="nsew")
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
        status.grid(row=4, column=0, sticky="ew", pady=(12, 0))
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

    def _ensure_preprocess_defaults(self, config: TabConfiguration) -> None:
        defaults = {
            "preproc.enabled": True,
            "preproc.device": "gpu",
            "preproc.convert_math": True,
            "preproc.convert_inline": True,
            "preproc.convert_tables": True,
            "preproc.min_area_px": 150,
            "preproc.margin_pts": 1.0,
            "preproc.pages": "",
            "preproc.dpi": 360,
        }
        for key, value in defaults.items():
            config.extras.setdefault(key, value)

    def _create_preprocess_controls(self, parent: ttk.Frame, tab_widget_id: str, config: TabConfiguration) -> None:
        frame = ttk.LabelFrame(parent, text="Preprocesado (convierte bloques a imágenes antes de Calibre)")
        frame.grid(row=2, column=0, sticky="nsew", pady=(0, 12))
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        # Create a scrollable frame for the preprocessing controls
        canvas = tk.Canvas(frame, highlightthickness=0, borderwidth=0)
        canvas.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        canvas.configure(yscrollincrement=20)

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(0, 8), pady=8)
        canvas.configure(yscrollcommand=scrollbar.set)

        inner = ttk.Frame(canvas)
        inner.columnconfigure(1, weight=1)
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(event: tk.Event) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _on_canvas_configure(event: tk.Event) -> None:
            # Adjust the width to account for the scrollbar
            canvas.itemconfigure(window_id, width=event.width)

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        self._bind_mousewheel(canvas, inner)
        
        # Make sure the canvas gets focus for mouse wheel scrolling
        canvas.bind("<Enter>", lambda e: canvas.focus_set())
        
        # Also bind mouse wheel to the inner frame to ensure it works anywhere in the area
        inner.bind("<Enter>", lambda e: canvas.focus_set())
        
        # Bind mouse wheel to the entire frame to ensure it works anywhere in the preprocessing area
        frame.bind("<Enter>", lambda e: canvas.focus_set())

        message = (
            "Rasteriza fórmulas y tablas problemáticas antes de invocar ebook-convert. "
            "Conviene mantenerlo activo para PDFs con contenido matemático."
        )
        ttk.Label(inner, text=message, wraplength=520, justify="left").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(6, 4)
        )

        controls: Dict[str, Any] = {"frame": frame, "canvas": canvas, "inner": inner}
        extras = config.extras

        # Add checkbox to enable/disable preprocessing
        enabled_var = tk.BooleanVar(value=bool(extras.get("preproc.enabled", True)))
        enabled_check = ttk.Checkbutton(inner, text="Activar preprocesado", variable=enabled_var)
        enabled_check.grid(row=1, column=0, columnspan=3, sticky="w", pady=4)

        device_var = tk.StringVar(value=str(extras.get("preproc.device", "gpu")))
        ttk.Label(inner, text="Dispositivo").grid(row=2, column=0, sticky="w", padx=(8, 6), pady=4)
        device_combo = ttk.Combobox(inner, textvariable=device_var, values=("gpu", "cpu"), state="readonly", width=8)
        device_combo.grid(row=2, column=1, sticky="w", padx=(0, 8), pady=4)

        dpi_var = tk.IntVar(value=int(extras.get("preproc.dpi", 360) or 360))
        ttk.Label(inner, text="DPI detectores").grid(row=3, column=0, sticky="w", padx=(8, 6), pady=4)
        tk.Spinbox(inner, from_=200, to=600, increment=20, textvariable=dpi_var, width=8).grid(
            row=3, column=1, sticky="w", padx=(0, 8), pady=4
        )

        convert_math_var = tk.BooleanVar(value=bool(extras.get("preproc.convert_math", True)))
        convert_inline_var = tk.BooleanVar(value=bool(extras.get("preproc.convert_inline", True)))
        convert_tables_var = tk.BooleanVar(value=bool(extras.get("preproc.convert_tables", True)))

        ttk.Checkbutton(inner, text="Convertir ecuaciones", variable=convert_math_var).grid(
            row=4, column=0, columnspan=2, sticky="w", padx=8, pady=4
        )
        ttk.Checkbutton(inner, text="Incluir ecuaciones inline", variable=convert_inline_var).grid(
            row=5, column=0, columnspan=2, sticky="w", padx=8, pady=4
        )
        ttk.Checkbutton(inner, text="Convertir tablas", variable=convert_tables_var).grid(
            row=6, column=0, columnspan=2, sticky="w", padx=8, pady=4
        )

        min_area_var = tk.IntVar(value=int(extras.get("preproc.min_area_px", 150) or 150))
        margin_var = tk.DoubleVar(value=float(extras.get("preproc.margin_pts", 1.0) or 1.0))
        ttk.Label(inner, text="Área mínima (px²)").grid(row=7, column=0, sticky="w", padx=(8, 6), pady=4)
        tk.Spinbox(inner, from_=0, to=50000, increment=10, textvariable=min_area_var, width=8).grid(
            row=7, column=1, sticky="w", padx=(0, 8), pady=4
        )
        ttk.Label(inner, text="Margen (pt)").grid(row=8, column=0, sticky="w", padx=(8, 6), pady=4)
        tk.Spinbox(inner, from_=0.0, to=20.0, increment=0.5, textvariable=margin_var, width=8).grid(
            row=8, column=1, sticky="w", padx=(0, 8), pady=4
        )

        pages_var = tk.StringVar(value=str(extras.get("preproc.pages", "")))
        ttk.Label(inner, text="Páginas a procesar").grid(row=9, column=0, sticky="w", padx=(8, 6), pady=4)
        ttk.Entry(inner, textvariable=pages_var).grid(row=9, column=1, sticky="ew", padx=(0, 8), pady=4)
        ttk.Label(inner, text="Ej: 2-5,10,13-17").grid(row=9, column=2, sticky="w", padx=(0, 8), pady=4)

        def _on_change(*_args: Any) -> None:
            cfg = self._config_by_tab[tab_widget_id]
            cfg.extras["preproc.enabled"] = bool(enabled_var.get())
            cfg.extras["preproc.device"] = device_var.get().strip().lower() or "cpu"
            try:
                cfg.extras["preproc.dpi"] = max(200, min(600, int(dpi_var.get())))
            except Exception:
                cfg.extras["preproc.dpi"] = 360
            cfg.extras["preproc.convert_math"] = bool(convert_math_var.get())
            cfg.extras["preproc.convert_inline"] = bool(convert_inline_var.get())
            cfg.extras["preproc.convert_tables"] = bool(convert_tables_var.get())
            try:
                cfg.extras["preproc.min_area_px"] = max(0, int(min_area_var.get()))
            except Exception:
                cfg.extras["preproc.min_area_px"] = 150
            try:
                cfg.extras["preproc.margin_pts"] = max(0.0, float(margin_var.get()))
            except Exception:
                cfg.extras["preproc.margin_pts"] = 1.0
            cfg.extras["preproc.pages"] = pages_var.get().strip()

        def _on_enabled_change(*_args: Any) -> None:
            # Enable/disable all controls based on the enabled checkbox
            state = "normal" if enabled_var.get() else "disabled"
            for widget in [device_combo, device_combo.master.winfo_children()[0]] + inner.winfo_children():
                if isinstance(widget, (ttk.Combobox, tk.Spinbox, ttk.Entry, ttk.Checkbutton)) and widget != enabled_check:
                    try:
                        widget.configure(state=state)
                    except tk.TclError:
                        pass
            _on_change()

        for var in (
            enabled_var,
            device_var,
            dpi_var,
            convert_math_var,
            convert_inline_var,
            convert_tables_var,
            min_area_var,
            margin_var,
            pages_var,
        ):
            var.trace_add("write", _on_change)
        
        enabled_var.trace_add("write", _on_enabled_change)

        controls.update(
            {
                "enabled_var": enabled_var,
                "device_var": device_var,
                "dpi_var": dpi_var,
                "convert_math_var": convert_math_var,
                "convert_inline_var": convert_inline_var,
                "convert_tables_var": convert_tables_var,
                "min_area_var": min_area_var,
                "margin_var": margin_var,
                "pages_var": pages_var,
            }
        )
        self._preprocess_controls[tab_widget_id] = controls
        
        # Initialize the state of controls based on the enabled checkbox
        # Use after_idle to ensure the tab is fully initialized before calling this
        self.after_idle(_on_enabled_change)

    def _sync_preprocess_controls(self, tab_widget_id: str) -> None:
        controls = self._preprocess_controls.get(tab_widget_id)
        if not controls:
            return
        cfg = self._config_by_tab[tab_widget_id]
        controls["enabled_var"].set(bool(cfg.extras.get("preproc.enabled", True)))
        controls["device_var"].set(str(cfg.extras.get("preproc.device", "gpu")))
        controls["convert_math_var"].set(bool(cfg.extras.get("preproc.convert_math", True)))
        controls["convert_inline_var"].set(bool(cfg.extras.get("preproc.convert_inline", True)))
        controls["convert_tables_var"].set(bool(cfg.extras.get("preproc.convert_tables", True)))
        controls["min_area_var"].set(int(cfg.extras.get("preproc.min_area_px", 150) or 150))
        controls["margin_var"].set(float(cfg.extras.get("preproc.margin_pts", 1.0) or 1.0))
        controls["pages_var"].set(str(cfg.extras.get("preproc.pages", "")))
        controls["dpi_var"].set(int(cfg.extras.get("preproc.dpi", 360) or 360))
        
        # Update the state of controls based on the enabled checkbox
        enabled = bool(cfg.extras.get("preproc.enabled", True))
        state = "normal" if enabled else "disabled"
        inner = controls["inner"]
        for widget in inner.winfo_children():
            if isinstance(widget, (ttk.Combobox, tk.Spinbox, ttk.Entry, ttk.Checkbutton)):
                # Skip the enabled checkbox itself
                if widget.cget("text") == "Activar preprocesado":
                    continue
                try:
                    widget.configure(state=state)
                except tk.TclError:
                    pass

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

    def _create_side_panel(self, parent: ttk.Frame, tab_widget_id: str, config: TabConfiguration) -> None:
        parent.rowconfigure(0, weight=3)
        parent.rowconfigure(1, weight=1)
        parent.rowconfigure(2, weight=0)
        parent.columnconfigure(0, weight=1)

        viewer_frame = ttk.LabelFrame(parent, text="Visor HTML")
        viewer_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        viewer_frame.columnconfigure(0, weight=1)
        viewer_frame.rowconfigure(0, weight=1)

        viewer = HtmlViewer(viewer_frame)
        viewer.grid(row=0, column=0, sticky="nsew", padx=6, pady=(6, 0))

        nav_frame = ttk.Frame(viewer_frame)
        nav_frame.grid(row=1, column=0, sticky="ew", padx=6, pady=(6, 0))
        nav_frame.columnconfigure(1, weight=1)

        prev_button = ttk.Button(
            nav_frame,
            text="Anterior",
            command=lambda tab_id=tab_widget_id: self._navigate_spine(tab_id, -1),
            state=tk.DISABLED,
        )
        prev_button.grid(row=0, column=0, padx=(0, 6))

        spine_var = tk.StringVar()
        spine_combo = ttk.Combobox(nav_frame, textvariable=spine_var, state="disabled")
        spine_combo.grid(row=0, column=1, sticky="ew")
        spine_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event, tab_id=tab_widget_id: self._on_spine_selected(tab_id),
        )

        next_button = ttk.Button(
            nav_frame,
            text="Siguiente",
            command=lambda tab_id=tab_widget_id: self._navigate_spine(tab_id, 1),
            state=tk.DISABLED,
        )
        next_button.grid(row=0, column=2, padx=(6, 0))

        reload_button = ttk.Button(
            viewer_frame,
            text="Recargar",
            command=lambda tab_id=tab_widget_id: self.reload_preview(tab_id),
        )
        reload_button.grid(row=2, column=0, sticky="w", padx=6, pady=6)

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
        self._spine_controls[tab_widget_id] = {
            "items": [],
            "labels": [],
            "current": None,
            "combo": spine_combo,
            "var": spine_var,
            "prev": prev_button,
            "next": next_button,
            "suspend": False,
        }

        # Espacio reservado para futuros avisos relacionados con preprocesado

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
        controls = self._spine_controls.get(tab_widget_id)
        if controls:
            controls["items"] = []
            controls["labels"] = []
            controls["current"] = None
            controls["suspend"] = True
            try:
                controls["var"].set("")
            finally:
                controls["suspend"] = False
            combo: ttk.Combobox = controls["combo"]
            combo.configure(values=(), state="disabled")
            controls["prev"].configure(state=tk.DISABLED)
            controls["next"].configure(state=tk.DISABLED)

    def _format_spine_label(self, oeb_root: Path, index: int, item: SpineItem) -> str:
        try:
            relative = item.href.relative_to(oeb_root)
            display = relative.as_posix()
        except ValueError:
            display = item.href.name
        display = display or item.idref
        return f"{index}. {display}"

    def _update_spine_controls(self, tab_widget_id: str, result: PreviewResult) -> None:
        controls = self._spine_controls.get(tab_widget_id)
        if controls is None:
            return
        items = list(result.spine_linear_items or ())
        if not items:
            items = [
                SpineItem(
                    idref=result.spine_first_html.name,
                    href=result.spine_first_html,
                    media_type="application/xhtml+xml",
                    linear=True,
                )
            ]
        controls["items"] = items
        labels = [self._format_spine_label(result.oeb_output, idx + 1, item) for idx, item in enumerate(items)]
        controls["labels"] = labels
        combo: ttk.Combobox = controls["combo"]
        combo.configure(values=labels)
        if items:
            combo.configure(state="readonly")
            previous = controls.get("current")
            target_index = 0 if previous is None else max(0, min(previous, len(items) - 1))
            self._set_spine_selection(tab_widget_id, target_index, load_viewer=True)
        else:
            controls["suspend"] = True
            try:
                controls["var"].set("")
            finally:
                controls["suspend"] = False
            combo.configure(state="disabled")
            controls["prev"].configure(state=tk.DISABLED)
            controls["next"].configure(state=tk.DISABLED)
            controls["current"] = None

    def _remove_tab(self, tab_id: str) -> None:
        self._cleanup_preview_state(tab_id)
        self._running_jobs.pop(tab_id, None)

        try:
            self.notebook.forget(tab_id)
        except tk.TclError:
            pass

        for registry in (
            self._config_by_tab,
            self._summary_labels,
            self._forms,
            self._status_labels,
            self._viewer_widgets,
            self._console_widgets,
            self._spine_controls,
            self._input_controls,
        ):
            registry.pop(tab_id, None)

        try:
            widget = self.nametowidget(tab_id)
        except (tk.TclError, KeyError):
            widget = None
        if widget is not None:
            try:
                widget.destroy()
            except tk.TclError:
                pass

    def _set_spine_selection(self, tab_widget_id: str, index: int, *, load_viewer: bool) -> None:
        controls = self._spine_controls.get(tab_widget_id)
        if not controls:
            return
        items: List[SpineItem] = controls.get("items", [])
        if not items:
            return
        clamped = max(0, min(index, len(items) - 1))
        label = controls["labels"][clamped] if clamped < len(controls["labels"]) else ""
        controls["current"] = clamped
        controls["suspend"] = True
        try:
            controls["var"].set(label)
        finally:
            controls["suspend"] = False
        controls["prev"].configure(state=tk.NORMAL if clamped > 0 else tk.DISABLED)
        controls["next"].configure(state=tk.NORMAL if clamped < len(items) - 1 else tk.DISABLED)
        if load_viewer:
            viewer = self._viewer_widgets.get(tab_widget_id)
            if viewer is not None:
                viewer.load(items[clamped].href)

    def _navigate_spine(self, tab_widget_id: str, delta: int) -> None:
        controls = self._spine_controls.get(tab_widget_id)
        if not controls or not controls.get("items"):
            return
        current = controls.get("current")
        base = 0 if current is None else current
        self._set_spine_selection(tab_widget_id, base + delta, load_viewer=True)

    def _on_spine_selected(self, tab_widget_id: str) -> None:
        controls = self._spine_controls.get(tab_widget_id)
        if not controls or controls.get("suspend"):
            return
        items = controls.get("items") or []
        if not items:
            return
        value = controls["var"].get()
        try:
            index = controls["labels"].index(value)
        except ValueError:
            return
        if controls.get("current") == index:
            return
        self._set_spine_selection(tab_widget_id, index, load_viewer=True)

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
        summary = self._format_preprocess_message(result.preprocess)
        if summary:
            pieces.append(summary)
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
        summary = self._format_preprocess_message(result.preprocess)
        if summary:
            pieces.append(summary)
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

    def _format_preprocess_message(self, preprocess: Any) -> str:
        if preprocess is None:
            return ""
        try:
            total = int(preprocess.replaced_regions)
            pages = tuple(preprocess.processed_pages)
            device = getattr(preprocess, "device", "?")
            workers = getattr(preprocess, "workers", 1)
        except Exception:
            return ""
        if total <= 0:
            return f"Preprocesado: sin cambios detectados (dispositivo {device})."
        pages_text = ", ".join(str(p) for p in pages) if pages else "n/a"
        return (
            f"Preprocesado: {total} regiones rasterizadas en {len(pages)} páginas "
            f"({pages_text}) · dispositivo {device} · lote máximo {workers}."
        )

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

        def runner(send: Callable[[str, Any], None], cancel_event: threading.Event) -> PreviewResult:
            if self._preview_runner is None:
                try:
                    options = options_from_extras(config.extras)
                except Exception:
                    options = None
                if options and options.should_process():
                    send("message", "Preprocesando PDF para fórmulas y tablas…")
                result = run_preview(
                    config,
                    catalog=self.catalog,
                    run=self._make_streaming_run(tab_id, send, cancel_event),
                )
                if result.preprocess is not None:
                    summary = self._format_preprocess_message(result.preprocess)
                    if summary:
                        send("message", summary)
                return result
            return self._preview_runner(config, cancel_event)

        def on_success(result: PreviewResult) -> None:
            self._cleanup_preview_state(tab_id)
            self._preview_state[tab_id] = result
            if tab_id in self._spine_controls:
                self._update_spine_controls(tab_id, result)
            else:
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

        def runner(send: Callable[[str, Any], None], cancel_event: threading.Event) -> ConversionResult:
            if self._conversion_runner is None:
                # Prefer packaging from an existing preview OEB if available.
                preview = self._preview_state.get(tab_id)
                if preview is not None and preview.oeb_output.exists():
                    self._append_console(tab_id, "Se detectó OEB de previsualización; empaquetando EPUB…")
                    if preview.preprocess is not None:
                        summary = self._format_preprocess_message(preview.preprocess)
                        if summary:
                            send("message", summary)
                    return package_epub_from_oeb(
                        config,
                        preview.oeb_output,
                        target_path,
                        catalog=self.catalog,
                        run=self._make_streaming_run(tab_id, send, cancel_event),
                    )
                # No preview available: run a preview (with preprocesado), then package
                try:
                    options = options_from_extras(config.extras)
                except Exception:
                    options = None
                if options and options.should_process():
                    send("message", "Preprocesando PDF antes de empaquetar…")
                prev = run_preview(
                    config,
                    catalog=self.catalog,
                    run=self._make_streaming_run(tab_id, send, cancel_event),
                )
                if prev.preprocess is not None:
                    summary = self._format_preprocess_message(prev.preprocess)
                    if summary:
                        send("message", summary)
                return package_epub_from_oeb(
                    config,
                    prev.oeb_output,
                    target_path,
                    catalog=self.catalog,
                    run=self._make_streaming_run(tab_id, send, cancel_event),
                )
            return self._conversion_runner(config, target_path, cancel_event)

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

"""Dynamic form generation for ebook-convert options grouped by category."""

from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable, Dict, Iterable, List, Optional

from core.configuration import TabConfiguration
from core.options.catalog import Catalog, OptionMetadata
from core.options.validator import OptionValidator

CATEGORY_ORDER = [
    "profiles",
    "look_and_feel",
    "heuristics",
    "search_replace",
    "structure",
    "toc",
    "metadata",
    "debug",
    "pdf_input",
    "epub_output",
]

TOOLTIP_BACKGROUND = "#13284B"
TOOLTIP_FOREGROUND = "#ECEFF4"


class Tooltip:
    """Simple tooltip bound to a widget."""

    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self.tipwindow: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._show)
        widget.bind("<Leave>", self._hide)

    def _show(self, event: tk.Event) -> None:  # pragma: no cover - UI behaviour
        if not self.text or self.tipwindow is not None:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.configure(background=TOOLTIP_BACKGROUND)
        label = tk.Label(
            tw,
            text=self.text,
            justify="left",
            background=TOOLTIP_BACKGROUND,
            foreground=TOOLTIP_FOREGROUND,
            relief="solid",
            borderwidth=1,
            padx=6,
            pady=4,
        )
        label.pack()

    def _hide(self, event: tk.Event) -> None:  # pragma: no cover - UI behaviour
        if self.tipwindow is not None:
            self.tipwindow.destroy()
            self.tipwindow = None


def _domain_values(option: OptionMetadata) -> Optional[Iterable[str]]:
    domain = option.domain or {}
    values = domain.get("values")
    if isinstance(values, list):
        return [str(value) for value in values]
    return None


def _domain_range(option: OptionMetadata) -> tuple[Optional[float], Optional[float]]:
    domain = option.domain or {}
    if domain.get("kind") == "range":
        return domain.get("min"), domain.get("max")
    return None, None


def _is_directory_option(option: OptionMetadata) -> bool:
    domain = option.domain or {}
    return domain.get("path_type") == "directory"


def _option_help_text(option: OptionMetadata) -> str:
    pieces = [option.description]
    if option.depends_on:
        deps = ", ".join(option.depends_on)
        pieces.append(f"Depende de: {deps}")
    if option.notes:
        pieces.append(option.notes)
    return "\n".join(filter(None, pieces))


class OptionField:
    """Widget wrapper bound to a TabConfiguration option."""

    def __init__(
        self,
        master: tk.Widget,
        option: OptionMetadata,
        config: TabConfiguration,
        validator: OptionValidator,
        on_change: Callable[[str], None],
    ) -> None:
        self.master = master
        self.option = option
        self.config = config
        self.validator = validator
        self.on_change = on_change
        self.tooltip_text = _option_help_text(option)
        self._suspend = False
        self._interactive_widgets: List[tuple[tk.Widget, str]] = []
        self._validation_errors: List[str] = []
        self._dependency_message = ""
        self._dependency_blocked = False

        self._current_label_wrap = 0
        self._current_error_wrap = 0

        self.label = ttk.Label(master, text=option.cli, anchor="w", justify="left")
        Tooltip(self.label, self.tooltip_text)

        if option.repeatable and option.value_type == "boolean":
            initial = int(config.options.get(option.id, 0) or 0)
            self.var = tk.IntVar(value=initial)
            self.widget = tk.Spinbox(
                master,
                from_=0,
                to=5,
                width=5,
                textvariable=self.var,
                command=self._on_spinbox_commit,
            )
            self.var.trace_add("write", self._on_variable_change)
            self._register_widget(self.widget, "normal")
        elif option.value_type == "boolean":
            initial = bool(config.options.get(option.id, False))
            self.var = tk.BooleanVar(value=initial)
            self.widget = ttk.Checkbutton(master, variable=self.var, text="Activar", command=self._on_checkbutton_toggle)
            self._register_widget(self.widget, "normal")
        elif option.value_type == "enum":
            values = list(_domain_values(option) or [])
            self.var = tk.StringVar(value=str(config.options.get(option.id, "")))
            self.widget = ttk.Combobox(master, textvariable=self.var, values=values, state="readonly" if values else "normal")
            self.widget.bind("<<ComboboxSelected>>", self._on_combobox_selected)
            self.var.trace_add("write", self._on_variable_change)
            mode = self.widget.cget("state") or "normal"
            self._register_widget(self.widget, mode)
        elif option.value_type in {"integer", "float"}:
            minimum, maximum = _domain_range(option)
            self.var = tk.StringVar(value=str(config.options.get(option.id, "")))
            if minimum is not None and maximum is not None:
                increment = 1 if option.value_type == "integer" else 0.5
                self.widget = tk.Spinbox(
                    master,
                    from_=minimum,
                    to=maximum,
                    increment=increment,
                    textvariable=self.var,
                )
                self._register_widget(self.widget, "normal")
            else:
                self.widget = ttk.Entry(master, textvariable=self.var)
                self._register_widget(self.widget, "normal")
            self.var.trace_add("write", self._on_variable_change)
        elif option.value_type == "path":
            self.var = tk.StringVar(value=str(config.options.get(option.id, "")))
            self.widget = ttk.Frame(master)
            entry = ttk.Entry(self.widget, textvariable=self.var)
            entry.pack(side="left", fill="x", expand=True)
            button = ttk.Button(self.widget, text="Explorar...", command=self._on_path_browse)
            button.pack(side="right", padx=(4, 0))
            self._register_widget(entry, "normal")
            self._register_widget(button, "normal")
            self.var.trace_add("write", self._on_variable_change)
        else:
            self.var = tk.StringVar(value=str(config.options.get(option.id, "")))
            self.widget = ttk.Entry(master, textvariable=self.var)
            self.var.trace_add("write", self._on_variable_change)
            self._register_widget(self.widget, "normal")

        self.help_label = ttk.Label(master, text="?", width=2, anchor="center")
        Tooltip(self.help_label, self.tooltip_text)

        self.error_label = ttk.Label(master, text="", wraplength=240, anchor="w", justify="left")

    def grid(self, row: int) -> None:
        self.label.grid(row=row, column=0, sticky="w", padx=(4, 6), pady=2)
        widget = self.widget
        if isinstance(widget, tk.Widget):
            widget.grid(row=row, column=1, sticky="ew", padx=(0, 6), pady=2)
        else:
            # For frames packed internally.
            pass
        self.help_label.grid(row=row, column=2, sticky="e", padx=(0, 4), pady=2)
        self.error_label.grid(row=row, column=3, sticky="w", padx=(0, 4), pady=2)
        self._revalidate()

    def update_wraplength(self, label_width: int, error_width: int) -> None:
        if label_width > 0 and label_width != self._current_label_wrap:
            self.label.configure(wraplength=label_width)
            self._current_label_wrap = label_width
        if error_width > 0 and error_width != self._current_error_wrap:
            self.error_label.configure(wraplength=error_width)
            self._current_error_wrap = error_width

    # -- Internal handlers ---------------------------------------------

    def _register_widget(self, widget: tk.Widget, active_state: str) -> None:
        self._interactive_widgets.append((widget, active_state))
        Tooltip(widget, self.tooltip_text)

    def _store_value(self, raw_value: Optional[str | int | bool]) -> None:
        key = self.option.id
        if self.option.value_type == "boolean" and not self.option.repeatable:
            if raw_value:
                self.config.options[key] = True
            else:
                self.config.options.pop(key, None)
        elif self.option.repeatable and self.option.value_type == "boolean":
            value = int(raw_value or 0)
            if value > 0:
                self.config.options[key] = value
            else:
                self.config.options.pop(key, None)
        else:
            if raw_value in ("", None):
                self.config.options.pop(key, None)
            else:
                self.config.options[key] = raw_value

        self._revalidate()
        self.on_change(key)

    def _on_variable_change(self, *_: str) -> None:
        if self._suspend:
            return
        value = self.var.get()
        self._store_value(value)

    def _on_checkbutton_toggle(self) -> None:
        if self._suspend:
            return
        self._store_value(bool(self.var.get()))

    def _on_combobox_selected(self, event: tk.Event) -> None:
        self._on_variable_change()

    def _on_spinbox_commit(self) -> None:
        self._on_variable_change()

    def _on_path_browse(self) -> None:  # pragma: no cover - UI interaction
        current = self.var.get()
        if _is_directory_option(self.option):
            selected = filedialog.askdirectory(initialdir=current or None, mustexist=False, title="Selecciona directorio")
        else:
            selected = filedialog.askopenfilename(initialdir=current or None, title="Selecciona archivo")
        if selected:
            self.var.set(selected)

    # -- External API --------------------------------------------------

    def sync_from_config(self) -> None:
        self._suspend = True
        try:
            value = self.config.options.get(self.option.id)
            if self.option.repeatable and self.option.value_type == "boolean":
                self.var.set(int(value or 0))
            elif self.option.value_type == "boolean":
                self.var.set(bool(value))
            else:
                if value is None:
                    self.var.set("")
                else:
                    self.var.set(str(value))
        finally:
            self._suspend = False
        self._revalidate()

    def set_dependency_state(self, missing: List[str]) -> None:
        if missing:
            self._dependency_message = "Activa primero: " + ", ".join(missing)
            self._dependency_blocked = True
            self._set_interactive_state(disabled=True)
        else:
            self._dependency_message = ""
            self._dependency_blocked = False
            self._set_interactive_state(disabled=False)
        self._revalidate()

    # -- Validation helpers --------------------------------------------

    def _set_interactive_state(self, *, disabled: bool) -> None:
        for widget, active_state in self._interactive_widgets:
            state = "disabled" if disabled else active_state or "normal"
            if isinstance(widget, ttk.Combobox):
                widget.configure(state=state)
            elif isinstance(widget, (ttk.Entry, ttk.Button, ttk.Checkbutton)):
                widget.configure(state=state)
            elif isinstance(widget, tk.Spinbox):
                widget.configure(state=state)
            else:
                try:
                    widget.configure(state=state)
                except tk.TclError:
                    continue

    def _revalidate(self) -> None:
        if self._dependency_blocked:
            self._validation_errors = []
        else:
            self._validation_errors = self.validator.validate(self.option, self.config)
        self._update_error_label()

    def _update_error_label(self) -> None:
        messages = []
        if self._dependency_message:
            messages.append(self._dependency_message)
        messages.extend(self._validation_errors)
        text = " | ".join(messages)
        self.error_label.configure(text=text)
        self.error_label.configure(style="Error.TLabel" if text else "TLabel")


class CategoryForm(ttk.LabelFrame):
    """Group of option fields for a specific category."""

    def __init__(
        self,
        master: tk.Widget,
        *,
        category_id: str,
        catalog: Catalog,
        config: TabConfiguration,
        validator: OptionValidator,
        on_change: Callable[[str], None],
    ) -> None:
        metadata = catalog.categories.get(category_id)
        label = metadata.label if metadata else category_id
        super().__init__(master, text=label)
        self.category_id = category_id
        self.catalog = catalog
        self.config = config
        self.on_change = on_change
        self.fields: Dict[str, OptionField] = {}
        self._description_label: Optional[ttk.Label] = None

        self.columnconfigure(1, weight=1)

        options = sorted(
            catalog.options_for_category(category_id),
            key=lambda opt: opt.cli,
        )
        for row, option in enumerate(options):
            field = OptionField(self, option, config, validator, on_change)
            field.grid(row=row)
            self.fields[option.id] = field

        if metadata and metadata.description:
            desc = ttk.Label(self, text=metadata.description, style="Small.TLabel", justify="left")
            desc.grid(row=len(options), column=0, columnspan=4, sticky="w", padx=4, pady=(4, 0))
            self._description_label = desc

        self.bind("<Configure>", self._on_resize, add="+")
        self.after_idle(self._apply_responsive_layout)

    def sync_from_config(self) -> None:
        for field in self.fields.values():
            field.sync_from_config()

    def apply_responsive_layout(self) -> None:
        self._apply_responsive_layout()

    def _on_resize(self, event: tk.Event) -> None:
        if event.width <= 1:
            return
        self._apply_responsive_layout(event.width)

    def _apply_responsive_layout(self, available_width: Optional[int] = None) -> None:
        width = available_width or self.winfo_width()
        if width <= 1:
            return

        label_width = max(160, int(width * 0.32))
        error_width = max(200, int(width * 0.45))
        for field in self.fields.values():
            field.update_wraplength(label_width, error_width)

        if self._description_label is not None:
            desc_wrap = max(200, width - 24)
            current = int(self._description_label.cget("wraplength") or 0)
            if desc_wrap != current:
                self._description_label.configure(wraplength=desc_wrap)


class ConfigForm(ttk.Frame):
    """Complete form with all categories rendered."""

    def __init__(
        self,
        master: tk.Widget,
        *,
        catalog: Catalog,
        config: TabConfiguration,
        on_change: Callable[[str], None],
    ) -> None:
        super().__init__(master)
        self.catalog = catalog
        self.config = config
        self.on_change = on_change
        self.validator = OptionValidator(catalog)
        self.sections: Dict[str, CategoryForm] = {}

        for index, category in enumerate(CATEGORY_ORDER):
            if category not in catalog.categories:
                continue
            section = CategoryForm(
                self,
                category_id=category,
                catalog=catalog,
                config=config,
                validator=self.validator,
                on_change=self._on_section_change,
            )
            section.grid(row=index, column=0, sticky="ew", padx=6, pady=6)
            section.columnconfigure(0, weight=0)
            section.columnconfigure(1, weight=1)
            section.columnconfigure(2, weight=0)
            section.columnconfigure(3, weight=1)
            self.sections[category] = section

        self.columnconfigure(0, weight=1)
        self.after_idle(self.update_responsive_layout)
        self._refresh_dependencies()

    def _on_section_change(self, option_id: str) -> None:
        self._refresh_dependencies()
        self.on_change(option_id)

    def sync_from_config(self) -> None:
        for section in self.sections.values():
            section.sync_from_config()
        self.update_responsive_layout()
        self._refresh_dependencies()

    def update_responsive_layout(self) -> None:
        for section in self.sections.values():
            section.apply_responsive_layout()

    def _refresh_dependencies(self) -> None:
        for section in self.sections.values():
            for field in section.fields.values():
                missing = self.validator.missing_dependencies(field.option, self.config)
                field.set_dependency_state(missing)

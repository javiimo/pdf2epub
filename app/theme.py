"""Application-wide theme configuration helpers."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from tkinter import font as tkfont

__all__ = ["apply_dark_nordic_theme"]

# Nord-inspired dark blue palette.
_COLORS = {
    "surface": "#2E3440",
    "surface_alt": "#3B4252",
    "surface_hover": "#434C5E",
    "surface_raised": "#4C566A",
    "border": "#4C566A",
    "foreground": "#ECEFF4",
    "muted": "#D8DEE9",
    "disabled_fg": "#707A8C",
    "accent": "#5E81AC",
    "accent_hover": "#6B90C4",
    "accent_pressed": "#4C6A92",
    "focus": "#88C0D0",
    "button_fg": "#E5E9F0",
    "selection_bg": "#88C0D0",
    "console_bg": "#1F2430",
    "warning_bg": "#13284B",
}


def _apply_widget_defaults(root: tk.Misc) -> None:
    """Configure Tk widget option patterns to match the dark palette."""

    defaults = {
        "*background": _COLORS["surface"],
        "*foreground": _COLORS["foreground"],
        "*highlightColor": _COLORS["focus"],
        "*selectForeground": _COLORS["surface"],
        "*selectBackground": _COLORS["selection_bg"],
        "*Entry.background": _COLORS["surface_alt"],
        "*Entry.foreground": _COLORS["foreground"],
        "*Entry.insertBackground": _COLORS["accent"],
        "*Spinbox.background": _COLORS["surface_alt"],
        "*Spinbox.foreground": _COLORS["foreground"],
        "*Spinbox.insertBackground": _COLORS["accent"],
        "*Text.background": _COLORS["console_bg"],
        "*Text.foreground": _COLORS["foreground"],
        "*Text.insertBackground": _COLORS["accent"],
        "*Listbox.background": _COLORS["surface_alt"],
        "*Listbox.foreground": _COLORS["foreground"],
        "*Listbox.selectBackground": _COLORS["selection_bg"],
        "*Listbox.selectForeground": _COLORS["surface"],
        "*Canvas.background": _COLORS["surface"],
    }
    for pattern, value in defaults.items():
        root.option_add(pattern, value)


def _configure_fonts(root: tk.Misc, base_font_size: int) -> None:
    """Update Tk named fonts to match the desired base size."""

    size = max(8, min(24, int(base_font_size)))
    mapping = {
        "TkDefaultFont": size,
        "TkTextFont": size,
        "TkMenuFont": size,
        "TkHeadingFont": size + 1,
        "TkFixedFont": size,
        "TkTooltipFont": size,
        "TkCaptionFont": size,
        "TkSmallCaptionFont": size,
        "TkIconFont": size,
    }
    for name, target_size in mapping.items():
        try:
            tkfont.nametofont(name).configure(size=target_size)
        except tk.TclError:
            continue


def _configure_ttk_styles(style: ttk.Style) -> None:
    """Apply ttk style overrides for the dark palette."""

    style.theme_use("clam" if "clam" in style.theme_names() else style.theme_use())

    base_kwargs = {"background": _COLORS["surface"], "foreground": _COLORS["foreground"]}
    style.configure(".", **base_kwargs)
    style.map(
        ".",
        foreground=[("disabled", _COLORS["disabled_fg"])],
        background=[("disabled", _COLORS["surface_alt"])],
    )

    style.configure("TFrame", background=_COLORS["surface"])
    style.configure("TLabel", background=_COLORS["surface"], foreground=_COLORS["foreground"])
    style.configure(
        "Error.TLabel",
        background=_COLORS["warning_bg"],
        foreground="#FF9B9B",
        relief="solid",
        borderwidth=1,
        padding=(6, 4),
    )
    style.map("Error.TLabel", foreground=[("disabled", _COLORS["disabled_fg"])])

    style.configure(
        "TLabelframe",
        background=_COLORS["surface"],
        foreground=_COLORS["muted"],
        bordercolor=_COLORS["border"],
        relief="solid",
    )
    style.configure(
        "TLabelframe.Label",
        background=_COLORS["surface"],
        foreground=_COLORS["muted"],
        padding=(6, 2),
    )

    style.configure(
        "TNotebook",
        background=_COLORS["surface"],
        bordercolor=_COLORS["border"],
        tabmargins=(6, 4, 6, 0),
    )
    style.configure(
        "TNotebook.Tab",
        background=_COLORS["surface_alt"],
        foreground=_COLORS["muted"],
        padding=(14, 6),
    )
    style.map(
        "TNotebook.Tab",
        background=[
            ("selected", _COLORS["surface"]),
            ("active", _COLORS["surface_hover"]),
        ],
        foreground=[
            ("selected", _COLORS["foreground"]),
            ("disabled", _COLORS["disabled_fg"]),
        ],
        bordercolor=[("selected", _COLORS["accent"])],
    )

    style.configure(
        "TButton",
        background=_COLORS["accent"],
        foreground=_COLORS["button_fg"],
        padding=(10, 6),
        borderwidth=0,
        focusthickness=2,
        focuscolor=_COLORS["focus"],
    )
    style.map(
        "TButton",
        background=[
            ("pressed", _COLORS["accent_pressed"]),
            ("active", _COLORS["accent_hover"]),
            ("disabled", _COLORS["surface_alt"]),
        ],
        foreground=[("disabled", _COLORS["disabled_fg"])],
    )

    style.configure(
        "TCheckbutton",
        background=_COLORS["surface"],
        foreground=_COLORS["foreground"],
        padding=(4, 2),
        indicatorsize=14,
    )
    style.map(
        "TCheckbutton",
        foreground=[("disabled", _COLORS["disabled_fg"])],
    )

    entry_kwargs = {
        "fieldbackground": _COLORS["surface_alt"],
        "foreground": _COLORS["foreground"],
        "insertcolor": _COLORS["accent"],
        "bordercolor": _COLORS["border"],
        "lightcolor": _COLORS["surface_alt"],
        "darkcolor": _COLORS["surface_alt"],
    }
    style.configure("TEntry", **entry_kwargs)
    style.map(
        "TEntry",
        fieldbackground=[
            ("readonly", _COLORS["surface_alt"]),
            ("disabled", _COLORS["surface_alt"]),
        ],
        foreground=[("disabled", _COLORS["disabled_fg"])],
    )

    combobox_kwargs = {
        "fieldbackground": _COLORS["surface_alt"],
        "foreground": _COLORS["foreground"],
        "background": _COLORS["surface_alt"],
        "insertcolor": _COLORS["accent"],
        "bordercolor": _COLORS["border"],
        "arrowcolor": _COLORS["accent"],
    }
    style.configure("TCombobox", **combobox_kwargs)
    style.map(
        "TCombobox",
        fieldbackground=[
            ("readonly", _COLORS["surface_alt"]),
            ("disabled", _COLORS["surface_alt"]),
        ],
        foreground=[("disabled", _COLORS["disabled_fg"])],
        arrowcolor=[
            ("active", _COLORS["accent_hover"]),
            ("disabled", _COLORS["disabled_fg"]),
        ],
    )

    style.configure(
        "TScrollbar",
        background=_COLORS["surface_alt"],
        troughcolor=_COLORS["surface"],
        bordercolor=_COLORS["surface"],
        arrowcolor=_COLORS["foreground"],
    )
    style.map(
        "TScrollbar",
        background=[
            ("active", _COLORS["surface_hover"]),
            ("disabled", _COLORS["surface_alt"]),
        ],
    )
    style.configure(
        "Vertical.TScrollbar",
        background=_COLORS["surface_alt"],
    )
    style.configure(
        "Horizontal.TScrollbar",
        background=_COLORS["surface_alt"],
    )

    style.configure("TSeparator", background=_COLORS["border"])


def apply_dark_nordic_theme(root: tk.Misc, *, base_font_size: int = 11) -> None:
    """Apply a Nord-inspired dark blue palette to the whole UI."""

    if isinstance(root, tk.Tk):
        root.configure(background=_COLORS["surface"])

    _apply_widget_defaults(root)
    _configure_fonts(root, base_font_size)
    style = ttk.Style(root)
    _configure_ttk_styles(style)

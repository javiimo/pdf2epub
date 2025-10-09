"""Simple dialog to select and apply presets."""

from __future__ import annotations

import tkinter as tk
from tkinter import simpledialog, ttk
from typing import Iterable, Optional

from core.presets import Preset


class _PresetDialog(simpledialog.Dialog):
    def __init__(self, parent: tk.Misc, presets: Iterable[Preset]):
        self._presets = list(presets)
        self._selected: Optional[Preset] = None
        super().__init__(parent, title="Seleccionar preset")

    def body(self, master: tk.Misc):
        master.columnconfigure(0, weight=1)
        master.rowconfigure(1, weight=1)

        ttk.Label(master, text="Elige un preset para aplicar sobre la pestaña actual:").grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 4)
        )

        self._listbox = tk.Listbox(master, height=8)
        self._listbox.grid(row=1, column=0, sticky="nsew", padx=8)
        self._listbox.bind("<<ListboxSelect>>", self._on_select)
        self._listbox.bind("<Double-Button-1>", self._on_double_click)

        scrollbar = ttk.Scrollbar(master, orient="vertical", command=self._listbox.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self._listbox.configure(yscrollcommand=scrollbar.set)

        for index, preset in enumerate(self._presets):
            self._listbox.insert(index, preset.name)

        self._description = ttk.Label(master, text="", wraplength=320, justify="left")
        self._description.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=(4, 8))

        if self._presets:
            self._listbox.selection_set(0)
            self._update_description(0)

        return self._listbox

    def buttonbox(self):  # pragma: no cover - standard dialog controls
        box = ttk.Frame(self)
        box.grid(row=3, column=0, columnspan=2, pady=8)

        ok_button = ttk.Button(box, text="Aplicar", width=10, command=self.ok, default=tk.ACTIVE)
        ok_button.pack(side="left", padx=5)
        cancel_button = ttk.Button(box, text="Cancelar", width=10, command=self.cancel)
        cancel_button.pack(side="left", padx=5)

        self.bind("<Return>", lambda event: self.ok())
        self.bind("<Escape>", lambda event: self.cancel())

    def _on_select(self, event: tk.Event) -> None:
        selection = self._listbox.curselection()
        if selection:
            self._update_description(selection[0])

    def _on_double_click(self, event: tk.Event) -> None:
        if self._listbox.curselection():
            self.ok()

    def _update_description(self, index: int) -> None:
        preset = self._presets[index]
        self._description.configure(text=preset.description)

    def apply(self) -> None:
        selection = self._listbox.curselection()
        if selection:
            self._selected = self._presets[selection[0]]

    @property
    def selection(self) -> Optional[Preset]:
        return self._selected


def choose_preset(master: tk.Misc, presets: Iterable[Preset]) -> Optional[Preset]:
    dialog = _PresetDialog(master, presets)
    return dialog.selection

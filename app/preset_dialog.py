"""Dialog to select one or more presets and return them ordered."""

from __future__ import annotations

import tkinter as tk
from tkinter import simpledialog, ttk
from typing import Iterable, List, Sequence

from core.presets import Preset

_LAYER_LABELS = {
    "base": "Base del dispositivo",
    "columns": "Columnas",
    "content": "Fórmulas / Imágenes",
    "toc": "Índice",
    "cleanup": "Limpieza",
    "debug": "Depuración",
    "custom": "Personalizado",
}


class _PresetDialog(simpledialog.Dialog):
    def __init__(self, parent: tk.Misc, presets: Iterable[Preset]):
        self._presets = list(presets)
        self._selected: List[Preset] = []
        super().__init__(parent, title="Seleccionar presets")

    def body(self, master: tk.Misc):
        master.columnconfigure(0, weight=1)
        master.rowconfigure(2, weight=1)

        ttk.Label(
            master,
            text="Selecciona uno o varios presets (Ctrl/Cmd+clic o Mayús+Clic).",
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(8, 2))
        ttk.Label(
            master,
            text="Se aplican en capas: Base → Columnas → Contenido → TOC → Limpieza → Debug.",
        ).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 6))

        self._listbox = tk.Listbox(master, height=12, selectmode=tk.MULTIPLE, exportselection=False)
        self._listbox.grid(row=2, column=0, sticky="nsew", padx=8)
        self._listbox.bind("<<ListboxSelect>>", self._on_select)
        self._listbox.bind("<Double-Button-1>", self._on_double_click)

        scrollbar = ttk.Scrollbar(master, orient="vertical", command=self._listbox.yview)
        scrollbar.grid(row=2, column=1, sticky="ns")
        self._listbox.configure(yscrollcommand=scrollbar.set)

        for index, preset in enumerate(self._presets):
            layer = _LAYER_LABELS.get(preset.layer, preset.layer.capitalize())
            self._listbox.insert(index, f"{preset.name} · {layer}")

        self._description = ttk.Label(master, text="", wraplength=360, justify="left")
        self._description.grid(row=3, column=0, columnspan=2, sticky="ew", padx=8, pady=(6, 8))

        if self._presets:
            self._listbox.selection_set(0)
            self._update_description((0,))

        return self._listbox

    def buttonbox(self):  # pragma: no cover - standard dialog controls
        box = ttk.Frame(self)
        box.pack(side="bottom", pady=8)

        ok_button = ttk.Button(box, text="Aplicar", width=10, command=self.ok, default=tk.ACTIVE)
        ok_button.pack(side="left", padx=5)
        cancel_button = ttk.Button(box, text="Cancelar", width=10, command=self.cancel)
        cancel_button.pack(side="left", padx=5)

        self.bind("<Return>", lambda event: self.ok())
        self.bind("<Escape>", lambda event: self.cancel())

    def _on_select(self, event: tk.Event) -> None:
        selection = self._listbox.curselection()
        self._update_description(selection)

    def _on_double_click(self, event: tk.Event) -> None:
        if self._listbox.curselection():
            self.ok()

    def _format_summary(self, indices: Sequence[int]) -> str:
        if not indices:
            return ""
        if len(indices) == 1:
            preset = self._presets[indices[0]]
            layer = _LAYER_LABELS.get(preset.layer, preset.layer.capitalize())
            return f"{preset.name} · {layer}\n{preset.description}"

        lines: List[str] = []
        for index in indices:
            preset = self._presets[index]
            layer = _LAYER_LABELS.get(preset.layer, preset.layer.capitalize())
            lines.append(f"• {preset.name} [{layer}]: {preset.description}")
        return "\n".join(lines)

    def _update_description(self, indices: Sequence[int]) -> None:
        summary = self._format_summary(indices)
        self._description.configure(text=summary)

    def apply(self) -> None:
        selection = sorted(self._listbox.curselection())
        if selection:
            self._selected = [self._presets[index] for index in selection]
        else:
            self._selected = []

    @property
    def selection(self) -> List[Preset]:
        return list(self._selected)


def choose_presets(master: tk.Misc, presets: Iterable[Preset]) -> List[Preset]:
    dialog = _PresetDialog(master, presets)
    return dialog.selection

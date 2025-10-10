"""Lightweight HTML viewer abstraction for the preview panel."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import ttk

try:  # pragma: no cover - we test fallback behaviour by default
    from tkinterweb import HtmlFrame  # type: ignore
except Exception:  # pragma: no cover - module may be missing
    HtmlFrame = None  # type: ignore[assignment]

__all__ = ["HtmlViewer"]


class HtmlViewer(ttk.Frame):
    """Embeddable HTML viewer with graceful fallback when tkinterweb is absent."""

    def __init__(self, master: tk.Misc, *, height: int = 20) -> None:
        super().__init__(master)
        self._last_path: Optional[Path] = None
        self._base_height = height
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        if HtmlFrame is not None:
            viewer = HtmlFrame(self, messages_enabled=False)
            viewer.grid(row=0, column=0, sticky="nsew")
            fallback: Optional[tk.Text] = None
        else:
            viewer = tk.Text(self, wrap="word", height=height, state="disabled")
            viewer.grid(row=0, column=0, sticky="nsew")
            scrollbar = ttk.Scrollbar(self, orient="vertical", command=viewer.yview)
            scrollbar.grid(row=0, column=1, sticky="ns")
            viewer.configure(yscrollcommand=scrollbar.set)
            fallback = viewer

        self._viewer = viewer
        self._fallback = fallback

    @property
    def last_path(self) -> Optional[Path]:
        return self._last_path

    def load(self, path: Path) -> None:
        """Render the given HTML file in the embedded viewer."""
        resolved = Path(path)
        self._last_path = resolved
        if HtmlFrame is not None and not self._fallback:
            self._viewer.load_file(str(resolved))
        else:
            assert self._fallback is not None
            self._fallback.configure(state="normal")
            try:
                self._fallback.delete("1.0", tk.END)
                try:
                    content = resolved.read_text(encoding="utf-8")
                except OSError as exc:
                    content = f"No se pudo leer el archivo: {exc}"
                self._fallback.insert(tk.END, content)
            finally:
                self._fallback.configure(state="disabled")

    def apply_font_scale(self, base_font_size: int) -> None:
        if self._fallback is None:
            return
        target = max(4, int(self._base_height))
        try:
            current = int(self._fallback.cget("height"))
        except (tk.TclError, ValueError, TypeError):
            current = target
        if current != target:
            self._fallback.configure(height=target)

    def reload(self) -> None:
        if self._last_path is None:
            return
        self.load(self._last_path)

    def reset(self) -> None:
        self._last_path = None
        if HtmlFrame is not None and not self._fallback:
            self._viewer.load_html("<html><body></body></html>")
        else:
            assert self._fallback is not None
            self._fallback.configure(state="normal")
            try:
                self._fallback.delete("1.0", tk.END)
            finally:
                self._fallback.configure(state="disabled")

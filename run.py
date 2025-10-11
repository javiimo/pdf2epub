#!/usr/bin/env python3
"""Punto de entrada para lanzar la interfaz gráfica de pdf2epub."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict

import tkinter as tk
from tkinter import messagebox

from app.notebook import ConfigNotebook
from app.theme import apply_dark_nordic_theme
from core.options.catalog import (
    Catalog,
    CatalogError,
    augment_with_detected_options,
    get_catalog,
    hide_unsupported_options,
    load_catalog,
)
from core.runner.dependencies import MissingDependencyError, verify_required_binaries
from core.runner.cli_support import get_cli_support_info
from core.settings import SettingsError, UiSettings, load_settings, save_settings


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interfaz gráfica para configurar conversiones PDF→EPUB con Calibre."
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        help="Ruta alternativa al catálogo de opciones (JSON). Por defecto se usa el incluido en assets/.",
    )
    return parser.parse_args(argv)


def _show_dependency_alert(root: tk.Tk, message: str, missing: Dict[str, str]) -> None:
    details = "\n".join(f"• {binary}: {info}" for binary, info in missing.items())
    messagebox.showerror(
        "Dependencias faltantes",
        f"{message}\n\nRevisa también:\n{details}",
        parent=root,
    )


def _load_catalog(path: Path | None) -> Catalog:
    if path is None:
        return get_catalog()
    return load_catalog(path)


def _safe_destroy(widget: tk.Misc) -> None:
    try:
        widget.destroy()
    except tk.TclError:
        pass


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"No se pudo inicializar Tk: {exc}", file=sys.stderr)
        return 1

    try:
        ui_settings = load_settings()
    except SettingsError as exc:
        print(f"Aviso: {exc}", file=sys.stderr)
        ui_settings = UiSettings()

    apply_dark_nordic_theme(
        root,
        base_font_size=ui_settings.font_size,
        base_font_family=ui_settings.font_family,
    )

    root.title("pdf2epub")
    root.minsize(1200, 720)
    root.withdraw()

    try:
        verify_required_binaries(
            alert_callback=lambda message, missing: _show_dependency_alert(root, message, missing)
        )
    except MissingDependencyError:
        _safe_destroy(root)
        return 1

    try:
        catalog = _load_catalog(args.catalog)
    except CatalogError as exc:
        messagebox.showerror("Catálogo inválido", str(exc), parent=root)
        _safe_destroy(root)
        return 1

    support_info = get_cli_support_info()
    supported_flags = support_info.flags if support_info is not None else None

    catalog, hidden_options = hide_unsupported_options(catalog, supported_flags)
    catalog, detected_options = augment_with_detected_options(catalog, support_info)
    if hidden_options:
        lines = [
            "Tu binario de ebook-convert no reconoce estas opciones y se ocultaron:",
            "",
        ]
        for option in hidden_options:
            lines.append(f"• {option.cli} — {option.description}")
        lines.extend(
            [
                "",
                "Actualiza Calibre si necesitas usar estas opciones.",
            ]
        )
        messagebox.showwarning("Opciones no soportadas", "\n".join(lines), parent=root)
    if detected_options:
        lines = [
            "Se detectaron opciones adicionales en tu versión de ebook-convert:",
            "",
        ]
        for option in detected_options:
            lines.append(f"• {option.cli}")
        lines.extend(
            [
                "",
                "Estas flags no existen en el catálogo oficial de la app; revisa su ayuda antes de usarlas.",
            ]
        )
        messagebox.showinfo("Nuevas opciones disponibles", "\n".join(lines), parent=root)

    def _handle_font_size_change(size: int) -> None:
        ui_settings.font_size = size
        apply_dark_nordic_theme(
            root,
            base_font_size=size,
            base_font_family=ui_settings.font_family,
        )
        notebook.refresh_layouts()
        save_settings(ui_settings)

    def _handle_font_family_change(family: str) -> None:
        ui_settings.font_family = family
        apply_dark_nordic_theme(
            root,
            base_font_size=ui_settings.font_size,
            base_font_family=family,
        )
        notebook.refresh_layouts()
        save_settings(ui_settings)

    notebook = ConfigNotebook(
        root,
        catalog=catalog,
        font_size=ui_settings.font_size,
        font_family=ui_settings.font_family,
        on_font_size_changed=_handle_font_size_change,
        on_font_family_changed=_handle_font_family_change,
    )
    notebook.pack(fill="both", expand=True)

    root.deiconify()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        _safe_destroy(root)
    return 0


if __name__ == "__main__":
    sys.exit(main())

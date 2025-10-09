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
from core.options.catalog import Catalog, CatalogError, get_catalog, load_catalog
from core.runner.dependencies import MissingDependencyError, verify_required_binaries


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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        print(f"No se pudo inicializar Tk: {exc}", file=sys.stderr)
        return 1

    root.title("pdf2epub")
    root.minsize(1200, 720)
    root.withdraw()

    try:
        verify_required_binaries(
            alert_callback=lambda message, missing: _show_dependency_alert(root, message, missing)
        )
    except MissingDependencyError:
        root.destroy()
        return 1

    try:
        catalog = _load_catalog(args.catalog)
    except CatalogError as exc:
        messagebox.showerror("Catálogo inválido", str(exc), parent=root)
        root.destroy()
        return 1

    notebook = ConfigNotebook(root, catalog=catalog)
    notebook.pack(fill="both", expand=True)

    root.deiconify()
    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())

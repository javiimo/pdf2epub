"""Dialog with Calibre update instructions tailored for LMDE systems."""

from __future__ import annotations

import platform
import re
import shutil
import ssl
import subprocess
import tkinter as tk
import urllib.error
import urllib.request
from typing import Optional
from tkinter import messagebox, ttk
import webbrowser

_INSTALL_COMMAND = (
    "sudo -v && wget -nv -O- https://download.calibre-ebook.com/linux-installer.sh | sudo sh /dev/stdin"
)
_REMOVE_DISTRIBUTION_COMMAND = "sudo apt purge calibre"
_UNINSTALL_COMMAND = "sudo calibre-uninstall"
_DOCS_URL = "https://calibre-ebook.com/download_linux"
_CALIBRE_VERSION_PATTERN = re.compile(
    r"(?:calibre|ebook-convert)\D*?([0-9]+(?:\.[0-9]+)+)",
    re.IGNORECASE,
)


def _copy_to_clipboard(widget: tk.Misc, text: str) -> None:
    widget.clipboard_clear()
    widget.clipboard_append(text)


def _run_in_terminal(command: str) -> bool:
    command_line = ["bash", "-lc", f"{command}; echo; read -n 1 -s -r -p 'Pulsa ENTER para cerrar...'"]
    candidates: list[tuple[str, list[str]]] = [
        ("kitty", ["-e"]),
        ("x-terminal-emulator", ["-e"]),
        ("gnome-terminal", ["--"]),
        ("xfce4-terminal", ["-e"]),
        ("mate-terminal", ["-e"]),
        ("konsole", ["-e"]),
        ("lxterminal", ["-e"]),
        ("tilix", ["-e"]),
        ("terminator", ["-e"]),
        ("alacritty", ["-e"]),
        ("urxvt", ["-e"]),
    ]

    for binary, launcher_args in candidates:
        path = shutil.which(binary)
        if not path:
            continue
        try:
            subprocess.Popen([path, *launcher_args, *command_line])
            return True
        except OSError:
            continue
    return False


def _detect_installed_version() -> Optional[str]:
    probes = [
        ("ebook-convert", "--version"),
        ("calibre", "--version"),
        ("calibre-debug", "--version"),
    ]
    for command in probes:
        try:
            completed = subprocess.run(
                list(command),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (FileNotFoundError, subprocess.SubprocessError, OSError):
            continue

        output = " ".join(filter(None, [completed.stdout, completed.stderr]))
        match = _CALIBRE_VERSION_PATTERN.search(output)
        if match:
            return match.group(1)
    return None


def _arch_for_tarball() -> str:
    machine = (platform.machine() or "").lower()
    if machine.startswith("arm") or machine.startswith("aarch64"):
        return "arm64"
    return "x86_64"


def _fetch_latest_version(timeout: float = 5.0) -> Optional[str]:
    url = f"https://code.calibre-ebook.com/tarball-info/{_arch_for_tarball()}"
    contexts = []
    try:
        ctx = ssl.create_default_context()
        contexts.append(ctx)
    except Exception:
        pass

    insecure_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    insecure_ctx.check_hostname = False
    insecure_ctx.verify_mode = ssl.CERT_NONE
    contexts.append(insecure_ctx)

    last_error: Optional[Exception] = None
    for ctx in contexts:
        try:
            with urllib.request.urlopen(url, timeout=timeout, context=ctx) as handle:
                raw = handle.read()
                if not raw:
                    continue
                version_bytes = raw.rpartition(b"@")[2].strip()
                if version_bytes:
                    return version_bytes.decode("utf-8", errors="replace")
        except (urllib.error.URLError, ValueError, OSError) as exc:
            last_error = exc
            continue
    if last_error:
        return None
    return None


def _open_docs(_: tk.Event) -> None:
    webbrowser.open(_DOCS_URL)


def _recommendation(installed: Optional[str], latest: Optional[str]) -> str:
    if latest and installed:
        if installed == latest:
            return "Recomendación: tu instalación está al día."
        return f"Recomendación: actualiza para pasar de {installed} a {latest}."
    if latest and not installed:
        return "Recomendación: instala Calibre usando el script oficial."
    if installed and not latest:
        return f"Recomendación: no se pudo comprobar la última versión. Tu versión actual es {installed}."
    return "Recomendación: no se pudo determinar ninguna versión."


def show_calibre_update_dialog(master: tk.Misc) -> None:
    installed_version = _detect_installed_version()
    latest_version = _fetch_latest_version()

    dialog = tk.Toplevel(master)
    dialog.title("Actualizar Calibre")
    dialog.transient(master)
    dialog.grab_set()
    dialog.resizable(False, False)

    container = ttk.Frame(dialog, padding=16)
    container.grid(row=0, column=0, sticky="nsew")
    container.columnconfigure(0, weight=1)

    intro = (
        "Para instalar o actualizar Calibre en LMDE (Linux Mint Debian Edition) "
        "usa el instalador oficial. Este proceso sustituye cualquier versión "
        "antigua instalada desde los repositorios."
    )
    ttk.Label(container, text=intro, wraplength=440, justify="left").grid(
        row=0, column=0, sticky="w"
    )

    versions_text = [
        f"Versión instalada: {installed_version if installed_version else 'No detectada'}",
        f"Última versión disponible: {latest_version if latest_version else 'Sin datos'}",
        _recommendation(installed_version, latest_version),
        "Tras actualizar, cierra y vuelve a abrir pdf2epub para que detecte la nueva versión.",
    ]
    ttk.Label(
        container,
        text="\n".join(versions_text),
        wraplength=440,
        justify="left",
    ).grid(row=1, column=0, sticky="w", pady=(12, 0))

    steps = [
        (
            "1. (Opcional) Elimina la versión de los repositorios:",
            _REMOVE_DISTRIBUTION_COMMAND,
        ),
        (
            "2. Instala o actualiza a la última versión estable:",
            _INSTALL_COMMAND,
        ),
        (
            "3. Para reinstalar, repite el comando anterior en cualquier momento.",
            None,
        ),
        (
            "4. Para desinstalar la versión instalada por el script oficial:",
            _UNINSTALL_COMMAND,
        ),
    ]

    next_row = 2
    for index, (description, command) in enumerate(steps):
        description_label = ttk.Label(container, text=description, wraplength=440, justify="left")
        pady_desc = (12, 2) if index == 0 else (8, 2)
        description_label.grid(row=next_row, column=0, sticky="w", pady=pady_desc)
        next_row += 1

        if command:
            command_frame = ttk.Frame(container)
            command_frame.grid(row=next_row, column=0, sticky="ew", padx=(24, 0), pady=(0, 12))
            command_frame.columnconfigure(0, weight=1)

            command_label = ttk.Label(command_frame, text=command, wraplength=410, justify="left")
            command_label.grid(row=0, column=0, sticky="w")

            def _execute(cmd: str) -> None:
                if not _run_in_terminal(cmd):
                    messagebox.showwarning(
                        "No se pudo abrir el terminal",
                        "No se encontró un emulador de terminal compatible. Copia el comando y ejecútalo manualmente.",
                        parent=dialog,
                    )

            button_holder = ttk.Frame(command_frame)
            button_holder.grid(row=1, column=0, sticky="e", pady=(8, 0))

            ttk.Button(
                button_holder,
                text="Copiar",
                command=lambda cmd=command: _copy_to_clipboard(dialog, cmd),
                width=10,
            ).pack(side="left", padx=(0, 6))
            ttk.Button(
                button_holder,
                text="Ejecutar",
                command=lambda cmd=command: _execute(cmd),
                width=10,
            ).pack(side="left")

            next_row += 1

    link = ttk.Label(
        container,
        text="Consulta la documentación oficial para más detalles.",
        foreground="#81a1c1",
        cursor="hand2",
        wraplength=420,
        justify="left",
    )
    link.grid(row=next_row, column=0, sticky="w", pady=(12, 0))
    link.bind("<Button-1>", _open_docs)
    next_row += 1

    ttk.Button(container, text="Cerrar", command=dialog.destroy).grid(
        row=next_row, column=0, sticky="e", pady=(18, 0)
    )

    dialog.update_idletasks()
    master_x = master.winfo_rootx()
    master_y = master.winfo_rooty()
    master_width = master.winfo_width()
    master_height = master.winfo_height()
    dialog_width = dialog.winfo_width()
    dialog_height = dialog.winfo_height()
    x = master_x + (master_width - dialog_width) // 2
    y = master_y + (master_height - dialog_height) // 2
    dialog.geometry(f"+{max(x, 0)}+{max(y, 0)}")

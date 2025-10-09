"""Helpers to introspect ebook-convert CLI capabilities."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence, Set

FLAG_PATTERN = re.compile(r"--[A-Za-z0-9][A-Za-z0-9_-]*")

_CACHE: Dict[str, Optional[Set[str]]] = {}


def _extract_flags(streams: Sequence[str]) -> Set[str]:
    flags: Set[str] = set()
    for chunk in streams:
        if not chunk:
            continue
        for match in FLAG_PATTERN.findall(chunk):
            flags.add(match)
    return flags


def _probe_supported_flags(
    executable: str,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Optional[Set[str]]:
    with tempfile.TemporaryDirectory(prefix="pdf2epub-probe-") as tmpdir:
        dummy_input = Path(tmpdir) / "dummy.txt"
        dummy_input.write_text("probe", encoding="utf-8")
        dummy_output = Path(tmpdir) / "dummy.epub"
        try:
            completed = run(
                [executable, str(dummy_input), str(dummy_output), "--help"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return None
    output = _extract_flags([completed.stdout, completed.stderr])
    return output or None


def get_supported_flags(
    executable: str = "ebook-convert",
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Optional[Set[str]]:
    """Return the set of supported CLI flags reported by ebook-convert.

    When probing the binary fails, returns ``None`` so callers can decide how
    to fall back (typically by keeping all catalogue options enabled).
    """
    cached = _CACHE.get(executable)
    if cached is not None:
        return cached
    flags = _probe_supported_flags(executable, run=run)
    _CACHE[executable] = flags
    return flags

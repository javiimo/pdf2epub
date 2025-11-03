"""Helpers to introspect ebook-convert CLI capabilities."""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Sequence, Set

FLAG_PATTERN = re.compile(r"--[A-Za-z0-9][A-Za-z0-9_-]*")


@dataclass(frozen=True)
class CliSupportInfo:
    """Structured information extracted from ``ebook-convert --help``."""

    flags: Set[str]
    help_by_flag: Dict[str, str]
    raw_output: str


_CACHE: Dict[str, Optional[CliSupportInfo]] = {}


def _extract_help_entries(stream: str) -> Dict[str, str]:
    entries: Dict[str, str] = {}
    if not stream:
        return entries

    lines = stream.splitlines()
    total = len(lines)
    index = 0
    while index < total:
        line = lines[index]
        matches = FLAG_PATTERN.findall(line)
        if not matches:
            index += 1
            continue

        block_lines = [line.rstrip()]
        look_ahead = index + 1
        while look_ahead < total:
            candidate = lines[look_ahead]
            stripped = candidate.lstrip()
            if not stripped:
                block_lines.append(candidate.rstrip())
                look_ahead += 1
                continue
            if stripped.startswith("-"):
                break
            block_lines.append(candidate.rstrip())
            look_ahead += 1

        block_text = "\n".join(block_lines).strip()
        for flag in matches:
            entries.setdefault(flag, block_text)

        index = look_ahead

    return entries


def _extract_flags(streams: Sequence[str]) -> Set[str]:
    flags: Set[str] = set()
    for chunk in streams:
        if not chunk:
            continue
        for match in FLAG_PATTERN.findall(chunk):
            flags.add(match)
    return flags


def _probe_cli_info(
    executable: str,
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Optional[CliSupportInfo]:
    with tempfile.TemporaryDirectory(prefix="pdf2epub-probe-") as tmpdir:
        tmp_path = Path(tmpdir)
        help_chunks = []
        streams = []
        scenarios = (
            ("dummy.pdf", b"%PDF-1.4\n%%EOF\n"),
            ("dummy.txt", b"probe\n"),
        )
        for filename, payload in scenarios:
            input_path = tmp_path / filename
            output_path = tmp_path / f"{input_path.stem}.epub"
            try:
                input_path.write_bytes(payload)
            except OSError:
                return None

            try:
                completed = run(
                    [executable, str(input_path), str(output_path), "--help"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except OSError:
                return None

            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            if stdout:
                help_chunks.append(stdout)
            if stderr:
                help_chunks.append(stderr)
            streams.extend(part for part in (stdout, stderr) if part)

    combined = "\n".join(help_chunks)

    help_entries = _extract_help_entries(combined)
    flags = set(help_entries)

    if not flags:
        flags = _extract_flags(streams)

    if not flags:
        return None

    return CliSupportInfo(flags=flags, help_by_flag=help_entries, raw_output=combined)


def get_cli_support_info(
    executable: str = "ebook-convert",
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Optional[CliSupportInfo]:
    """Return structured help information for the given executable."""

    cached = _CACHE.get(executable)
    if cached is not None:
        return cached

    info = _probe_cli_info(executable, run=run)
    _CACHE[executable] = info
    return info


def get_supported_flags(
    executable: str = "ebook-convert",
    *,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> Optional[Set[str]]:
    """Return the set of supported CLI flags reported by ebook-convert."""

    info = get_cli_support_info(executable, run=run)
    if info is None:
        return None
    return set(info.flags)

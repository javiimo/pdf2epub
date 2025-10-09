"""Execution engine for ebook-convert and auxiliary tools."""

from .dependencies import MissingDependencyError, verify_required_binaries
from .pdf_subset import PdfSubsetError, prepare_pdf_subset
from .preview import PreviewError, PreviewResult, run_preview

__all__ = [
    "MissingDependencyError",
    "PdfSubsetError",
    "PreviewError",
    "PreviewResult",
    "prepare_pdf_subset",
    "run_preview",
    "verify_required_binaries",
]

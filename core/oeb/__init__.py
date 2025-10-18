"""OEB assembly utilities (inserting images and updating OPF)."""

from .assemble import (
    AssemblyError,
    add_images_to_manifest,
    insert_figures_into_html,
    copy_images_into_oeb,
)

__all__ = [
    "AssemblyError",
    "add_images_to_manifest",
    "insert_figures_into_html",
    "copy_images_into_oeb",
]


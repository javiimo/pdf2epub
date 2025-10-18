"""OEB assembly utilities (inserting images and updating OPF)."""

from .assemble import (
    AssemblyError,
    add_images_to_manifest,
    insert_figures_into_html,
    copy_images_into_oeb,
    write_css_into_oeb,
    add_css_to_manifest,
    link_stylesheet_in_html,
    install_default_css,
)

__all__ = [
    "AssemblyError",
    "add_images_to_manifest",
    "insert_figures_into_html",
    "copy_images_into_oeb",
    "write_css_into_oeb",
    "add_css_to_manifest",
    "link_stylesheet_in_html",
    "install_default_css",
]

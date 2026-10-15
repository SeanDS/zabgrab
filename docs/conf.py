"""Sphinx configuration for zabgrab's documentation."""

from __future__ import annotations

from datetime import date

from zabgrab import __version__

project = "zabgrab"
author = "Sean Leavey"
copyright = f"{date.today().year}, {author}"  # noqa: DTZ011 - just the year
# The full version, e.g. 1.0.0 or 1.0.1.dev3+g1234abc between releases.
release = __version__
version = ".".join(release.split(".")[:2])

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx_click",
]

exclude_patterns = ["_build"]

# NumPy-style docstrings only.
napoleon_google_docstring = False
napoleon_use_rtype = False
autodoc_typehints = "description"
autodoc_member_order = "bysource"
autodoc_default_options = {"members": True, "show-inheritance": True}
autosummary_generate = True

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "click": ("https://click.palletsprojects.com/en/stable/", None),
}

html_theme = "alabaster"
html_static_path = ["_static"]
html_title = f"zabgrab {release}"
html_theme_options = {
    "description": "Fetch data from Zabbix into MATLAB, HDF5 and CSV files.",
    "github_user": "SeanDS",
    "github_repo": "zabgrab",
    "github_button": True,
    "github_type": "star",
    "fixed_sidebar": True,
    "show_relbars": True,
    "page_width": "1100px",
    "sidebar_width": "240px",
}
html_sidebars = {
    "**": ["about.html", "navigation.html", "relations.html", "searchbox.html"]
}

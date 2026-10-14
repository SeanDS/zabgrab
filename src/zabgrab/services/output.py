"""Deciding where and how to save fetched data."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..errors import Cancelled, ZabgrabError
from ..model.config import Format
from ..model.query import Query
from .naming import EXTENSIONS, format_for, output_name, unique_path
from .ports import UI, Option

#: Formats that can be written so far.
AVAILABLE = (Format.TSV, Format.CSV)

FORMAT_FIX = (
    "Pass --format (tsv or csv), give --output a file name ending in .tsv or .csv, or "
    "set format in the template or [defaults]."
)


@dataclass(frozen=True)
class Target:
    path: Path
    format: Format


def auto_name(query: Query, *, hosts: Sequence[str], servers: Sequence[str]) -> str:
    """The automatic file name (without extension) for a query's data."""
    return output_name(
        query.output_pattern,
        hosts=hosts,
        templates=query.templates,
        custom=query.custom,
        start=query.start,
        end=query.end,
        servers=servers,
        resolution=str(query.resolution),
    )


def targets(
    output: str | None,
    query: Query,
    ui: UI,
    *,
    name: str,
    cli_format: Format | None,
    overwrite: bool,
) -> list[Target]:
    """Where to save the data.

    ``output`` is the ``--output`` value: ``None`` (not given: ask, or don't save
    non-interactively), ``""`` (automatic name in ``output_dir``), a directory
    (automatic name there) or a file.
    """
    if output is None:
        if not ui.interactive:
            ui.warning("The data wasn't saved; pass --output to save it.")
            return []
        formats = _formats(query, cli_format, None, ui)
        shown = ", ".join(f"{name}{EXTENSIONS[f]}" for f in formats)
        if not ui.confirm(f"Save the data to {shown}?", fix="", default=True):
            return []
        directory, file = Path(query.output_dir), None
    elif output == "" or output.endswith(("/", "\\")) or Path(output).is_dir():
        directory, file = Path(output or query.output_dir), None
        formats = _formats(query, cli_format, None, ui)
    else:
        file = Path(output)
        directory = file.parent
        formats = _formats(query, cli_format, file, ui)

    for format in formats:
        if format not in AVAILABLE:
            raise ZabgrabError(
                f"Saving as {format} isn't available yet.",
                hint="Use tsv or csv for now (--format, or a .tsv/.csv file name).",
            )

    chosen = []
    for format in formats:
        if file is not None and len(formats) == 1:
            path = file
            chosen.append(Target(_existing(path, ui, overwrite=overwrite), format))
        else:
            stem = file.stem if file is not None else name
            path = directory / f"{stem}{EXTENSIONS[format]}"
            if path.exists() and not overwrite:
                ui.info(f"{path} already exists; saving to {unique_path(path)}.")
                path = unique_path(path)
            chosen.append(Target(path, format))
    return chosen


def _formats(
    query: Query, cli_format: Format | None, file: Path | None, ui: UI
) -> tuple[Format, ...]:
    if cli_format is not None:
        if file is not None and format_for(file) not in (None, cli_format):
            ui.warning(f"Saving {cli_format} data to {file}, despite its extension.")
        return (cli_format,)
    if file is not None and (from_extension := format_for(file)) is not None:
        return (from_extension,)
    if query.formats:
        return query.formats
    return (
        ui.choose(
            "Which format should the data be saved in?",
            [
                Option("tsv", Format.TSV, "tab-separated values"),
                Option("csv", Format.CSV, "comma-separated values"),
            ],
            fix=FORMAT_FIX,
            default=0,
        ),
    )


def _existing(path: Path, ui: UI, *, overwrite: bool) -> Path:
    if overwrite or not path.exists():
        return path
    alternative = unique_path(path)
    if not ui.interactive:
        ui.warning(
            f"{path} already exists; saving to {alternative} instead. Pass "
            "--overwrite to replace it."
        )
        return alternative
    options: list[Option[Path | None]] = [
        Option("Overwrite it", path),
        Option(f"Save to {alternative}", alternative),
        Option("Cancel", None),
    ]
    choice = ui.choose(
        f"{path} already exists.",
        options,
        fix="",
        default=1,
    )
    if choice is None:
        raise Cancelled()
    return choice

"""Deciding where and how to save fetched data."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from ..errors import Cancelled, ZabgrabError
from ..model.answers import Answers
from ..model.config import Format
from ..model.query import Query
from ..model.series import FetchResult, Skipped
from .naming import EXTENSIONS, format_for, output_name, unique_path
from .ports import UI, Option

#: Formats that hold several fetches (as groups), so can be appended to.
STRUCTURED = (Format.MAT, Format.H5)

#: Measured costs of a text entry in a .mat file (see benchmarks/mat_text.py).
MAT_TEXT_BYTES = 480
MAT_TEXT_WRITE_SECONDS = 60e-6

FORMAT_FIX = (
    "Pass --format, give --output a file name ending in .mat, .h5, .tsv or .csv, or "
    "set format in the template or [defaults]."
)


@dataclass(frozen=True)
class Target:
    path: Path
    format: Format
    append: bool = False
    """Add a group to the file (which may not exist yet), rather than replace it."""
    large_text_accepted: bool = False


@dataclass(frozen=True)
class SaveOptions:
    """Command line options about saving."""

    format: Format | None = None
    overwrite: bool = False
    append: bool = False
    group: str | None = None
    in_place: bool = False
    allow_large_text: bool = False

    def check(self, output: str | None) -> None:
        """Reject options that don't make sense together."""
        if self.append and self.overwrite:
            raise ZabgrabError("--append and --overwrite can't be used together.")
        if (self.append or self.group or self.in_place) and not output:
            raise ZabgrabError(
                "--append, --group and --in-place need --output with a file name.",
                hint="To add new data to a file fetched before, use "
                "`zabgrab update FILE`.",
            )
        if (self.group or self.in_place) and not self.append:
            raise ZabgrabError("--group and --in-place only apply with --append.")
        if self.append and output:
            format = self.format or format_for(Path(output))
            if format is not None and format not in STRUCTURED:
                raise ZabgrabError(
                    "Only .mat and .h5 files can be appended to.",
                    hint="Use --output with a .mat or .h5 file name.",
                )


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
    options: SaveOptions,
) -> list[Target]:
    """Where to save the data.

    ``output`` is the ``--output`` value: ``None`` (not given: ask, or don't save
    non-interactively), ``""`` (automatic name in ``output_dir``), a directory
    (automatic name there) or a file.
    """
    options.check(output)
    if output is None:
        if not ui.interactive:
            ui.warning("The data wasn't saved; pass --output to save it.")
            return []
        formats = choose_formats(query.formats, options.format, None, ui, query.answers)
        shown = ", ".join(f"{name}{EXTENSIONS[f]}" for f in formats)
        if not ui.confirm(f"Save the data to {shown}?", fix="", default=True):
            return []
        directory, file = Path(query.output_dir), None
    elif output == "" or output.endswith(("/", "\\")) or Path(output).is_dir():
        directory, file = Path(output or query.output_dir), None
        formats = choose_formats(query.formats, options.format, None, ui, query.answers)
    else:
        file = Path(output)
        directory = file.parent
        formats = choose_formats(query.formats, options.format, file, ui, query.answers)

    if options.append and any(f not in STRUCTURED for f in formats):
        raise ZabgrabError(
            "Only .mat and .h5 files can be appended to.",
            hint="Use --output with a .mat or .h5 file name.",
        )

    chosen = []
    for format in formats:
        if file is not None and len(formats) == 1:
            chosen.append(existing_target(file, format, ui, options))
        else:
            stem = file.stem if file is not None else name
            path = directory / f"{stem}{EXTENSIONS[format]}"
            if path.exists() and not (options.overwrite or options.append):
                ui.info(f"{path} already exists; saving to {unique_path(path)}.")
                path = unique_path(path)
            chosen.append(Target(path, format, append=options.append))
    return chosen


def choose_formats(
    configured: Sequence[Format],
    cli_format: Format | None,
    file: Path | None,
    ui: UI,
    answers: Answers | None = None,
) -> tuple[Format, ...]:
    """The output format(s): ``--format``, else the file's extension, else the
    configured ones, else asked (the answer is recorded in ``answers``)."""
    if cli_format is not None:
        if file is not None and format_for(file) not in (None, cli_format):
            ui.warning(f"Saving {cli_format} data to {file}, despite its extension.")
        return (cli_format,)
    if file is not None and (from_extension := format_for(file)) is not None:
        return (from_extension,)
    if configured:
        return tuple(configured)
    chosen = ui.choose(
        "Which format should the data be saved in?",
        [
            Option("mat", Format.MAT, "MATLAB (v7.3)"),
            Option("h5", Format.H5, "HDF5"),
            Option("tsv", Format.TSV, "tab-separated values"),
            Option("csv", Format.CSV, "comma-separated values"),
        ],
        fix=FORMAT_FIX,
        default=0,
    )
    if answers is not None:
        answers.settings["format"] = [chosen.value]
    return (chosen,)


def existing_target(path: Path, format: Format, ui: UI, options: SaveOptions) -> Target:
    """Where to save to ``path``, asking what to do if it exists."""
    if options.overwrite or options.append or not path.exists():
        return Target(path, format, append=options.append)
    alternative = unique_path(path)
    if not ui.interactive:
        ui.warning(
            f"{path} already exists; saving to {alternative} instead. Pass "
            "--overwrite to replace it"
            + (", or --append to add to it." if format in STRUCTURED else ".")
        )
        return Target(alternative, format)

    choices: list[Option[Target | None]] = [
        Option("Overwrite it", Target(path, format)),
        Option(f"Save to {alternative}", Target(alternative, format)),
        Option("Cancel", None),
    ]
    if format in STRUCTURED:
        choices.insert(
            0, Option("Add the data to it as a new group", Target(path, format, True))
        )
    choice = ui.choose(
        f"{path} already exists.",
        choices,
        fix="",
        default=0 if format in STRUCTURED else 1,
    )
    if choice is None:
        raise Cancelled()
    return choice


def text_entries(result: FetchResult) -> int:
    """How many text values a fetch has (each is a cell entry in a .mat file)."""
    count = 0
    for series in result.series:
        if not series.item.value_type.numeric and "value" in series.columns:
            count += len(series.columns["value"])
        count += len(series.columns.get("source", ()))
    return count


def guard_large_text(
    target: Target,
    result: FetchResult,
    ui: UI,
    *,
    threshold: int,
    allow: bool,
    existing_entries: Callable[[Path], int],
    accepted_before: Callable[[Path], bool],
) -> tuple[Target, FetchResult]:
    """Warn before writing a lot of text to a .mat file, which makes it big and
    slow to load in MATLAB.

    Interactive users can continue, save as .h5 instead, skip the text items or
    cancel. Others get a warning. Accepting (or ``allow``) is remembered in the
    file, so later appends don't ask again. A ``threshold`` of 0 never warns.
    """
    new = text_entries(result)
    if target.format is not Format.MAT or not new or threshold == 0:
        return target, result

    appending = target.append and target.path.exists()
    if allow or (appending and accepted_before(target.path)):
        return replace(target, large_text_accepted=True), result
    total = new + (existing_entries(target.path) if appending else 0)
    if total <= threshold:
        return target, result

    message = (
        f"{target.path} would hold {total:,} text values, more than "
        f"mat_text_warn_entries ({threshold:,}). In .mat files each is stored "
        f"separately: this adds about {new * MAT_TEXT_BYTES / 1e6:.0f} MB (several "
        "times more than .h5) and makes the file slower to write and load."
    )
    if not ui.interactive:
        ui.warning(
            f"{message} Continuing; pass --allow-large-text to accept this, or save "
            "as .h5 instead."
        )
        return target, result

    ui.warning(message)
    h5 = target.path.with_suffix(".h5")
    choice = ui.choose(
        "What would you like to do?",
        [
            Option(
                "Continue",
                "continue",
                f"about {new * MAT_TEXT_WRITE_SECONDS:.0f} s to write",
            ),
            Option(f"Save as {h5} instead", "h5", "HDF5 stores text efficiently"),
            Option("Leave out the text items", "skip"),
            Option("Cancel", "cancel"),
        ],
        fix="",
        default=0,
    )
    if choice == "cancel":
        raise Cancelled()
    if choice == "h5":
        return Target(h5, Format.H5, append=target.append), result
    if choice == "skip":
        kept = [s for s in result.series if s.item.value_type.numeric]
        skipped = [
            Skipped(s.server, s.item, "text left out of the .mat file")
            for s in result.series
            if not s.item.value_type.numeric
        ]
        ui.info(f"Leaving out {len(skipped)} text item(s).")
        return target, replace(result, series=kept, skipped=[*result.skipped, *skipped])
    return replace(target, large_text_accepted=True), result

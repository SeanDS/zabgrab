"""Deciding where and how to save fetched data.

Attributes
----------
STRUCTURED : tuple of Format
    Formats that hold several fetches (as groups), so can be appended to.
MAT_TEXT_BYTES : int
    The measured size of a text entry in a .mat file (see
    ``benchmarks/mat_text.py``).
MAT_TEXT_WRITE_SECONDS : float
    The measured time to write a text entry to a .mat file.
FORMAT_FIX : str
    How to choose the format without being asked.
"""

from __future__ import annotations

import os
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

STRUCTURED = (Format.MAT, Format.H5)

MAT_TEXT_BYTES = 480
MAT_TEXT_WRITE_SECONDS = 60e-6

FORMAT_FIX = (
    "Pass --format, give --output a file name ending in .h5, .mat, .tsv or .csv, or "
    "set format in the template or [defaults]."
)


@dataclass(frozen=True)
class Target:
    """A file to save data to.

    Attributes
    ----------
    path : Path
        The file.
    format : Format
        Its format.
    append : bool
        Add a group to the file (which may not exist yet), rather than replace it.
    large_text_accepted : bool
        The user accepted writing a lot of text to a .mat file, which is recorded in
        it so later appends don't ask again.
    """

    path: Path
    format: Format
    append: bool = False
    large_text_accepted: bool = False


@dataclass(frozen=True)
class SaveOptions:
    """Command line options about saving.

    Attributes
    ----------
    format : Format or None
        ``--format``.
    overwrite : bool
        ``--overwrite``.
    append : bool
        ``--append``.
    group : str or None
        ``--group``: the name of the group to append.
    in_place : bool
        ``--in-place``: append without copying the file first.
    allow_large_text : bool
        ``--allow-large-text``.
    plot : bool
        ``--plot``: the data will be plotted, so without ``--output`` it's neither
        saved nor asked about.
    """

    format: Format | None = None
    overwrite: bool = False
    append: bool = False
    group: str | None = None
    in_place: bool = False
    allow_large_text: bool = False
    plot: bool = False

    def check(self, outputs: Sequence[str]) -> None:
        """Reject options that don't make sense together.

        Parameters
        ----------
        outputs : sequence of str
            The ``--output`` values given (none if it wasn't).

        Raises
        ------
        ZabgrabError
            If the options conflict.
        """
        if self.append and self.overwrite:
            raise ZabgrabError("--append and --overwrite can't be used together.")
        if (self.append or self.group or self.in_place) and (
            not outputs or "" in outputs
        ):
            raise ZabgrabError(
                "--append, --group and --in-place need --output with a file name.",
                hint="To add new data to a file fetched before, use "
                "`zabgrab update FILE`.",
            )
        if (self.group or self.in_place) and not self.append:
            raise ZabgrabError("--group and --in-place only apply with --append.")
        for output in outputs if self.append else ():
            format = self.format or format_for(Path(output))
            if format is not None and format not in STRUCTURED:
                raise ZabgrabError(
                    "Only .mat and .h5 files can be appended to.",
                    hint="Use --output with a .mat or .h5 file name.",
                )


def auto_name(query: Query, *, hosts: Sequence[str], server: str) -> str:
    """Make the automatic file name (without extension) for a query's data.

    Parameters
    ----------
    query : Query
        The query, whose ``output_pattern`` is filled in.
    hosts : sequence of str
        The names of the hosts the data comes from.
    server : str
        A short name for the server the data comes from.

    Returns
    -------
    str
        The name.
    """
    return output_name(
        query.output_pattern,
        hosts=hosts,
        template=query.template,
        custom=query.custom,
        start=query.start,
        end=query.end,
        server=server,
        resolution=str(query.resolution),
    )


def targets(
    outputs: Sequence[str],
    query: Query,
    ui: UI,
    *,
    name: str,
    options: SaveOptions,
) -> list[Target]:
    """Decide where to save the data.

    Parameters
    ----------
    outputs : sequence of str
        The ``--output`` values, each saved to: ``""`` for an automatic name in
        ``output_dir``, a directory for an automatic name there, or a file. With
        none, the user is asked (or, if the session isn't interactive or the data is
        plotted, it isn't saved).
    query : Query
        The query, for its configured formats and output directory.
    ui : UI
        For questions and messages.
    name : str
        The automatic file name (see :func:`auto_name`).
    options : SaveOptions
        The other command line options about saving.

    Returns
    -------
    list of Target
        The files to write (one per output and format, each path once); empty if
        the data isn't to be saved.

    Raises
    ------
    ZabgrabError
        If the options conflict.
    Cancelled
        If the user cancels.
    """
    options.check(outputs)
    # The formats to use unless --format or a file extension says otherwise.
    known: Sequence[Format] = query.formats
    if not outputs:
        if options.plot:
            return []
        if not ui.interactive:
            ui.warning("The data wasn't saved; pass --output to save it.")
            return []
        known = choose_formats(query.formats, options.format, None, ui, query.answers)
        shown = ", ".join(f"{name}{EXTENSIONS[f]}" for f in known)
        answer = ui.prompt(
            f"Save the data to {shown}? Yes, no, or to another path [y/n/o]",
            fix="",
            parse=_save_answer,
            default="y",
        )
        if answer == "n":
            return []
        # Either way, carry on as if the path had been given with --output, keeping
        # the formats just chosen.
        outputs = (
            [""]
            if answer == "y"
            else [
                ui.prompt(
                    "Save to (a file, or a directory ending in /)", fix="", parse=_path
                )
            ]
        )

    chosen: dict[Path, Target] = {}
    for output in outputs:
        found = _output_targets(output, known, query, ui, name=name, options=options)
        for target in found:
            chosen.setdefault(target.path, target)
        # Formats asked for (for want of an extension) apply to the other outputs
        # too, rather than being asked for again.
        if (
            not known
            and options.format is None
            and (_is_directory(output) or format_for(Path(output)) is None)
        ):
            known = tuple(target.format for target in found)
    return list(chosen.values())


def _is_directory(output: str) -> bool:
    """Tell whether an ``--output`` value means a directory (automatic names)."""
    return output == "" or output.endswith(("/", "\\")) or Path(output).is_dir()


def _output_targets(
    output: str,
    known: Sequence[Format],
    query: Query,
    ui: UI,
    *,
    name: str,
    options: SaveOptions,
) -> list[Target]:
    """Decide where to save for one ``--output`` value (see :func:`targets`)."""
    if _is_directory(output):
        directory, file = Path(output or query.output_dir).expanduser(), None
        formats = choose_formats(known, options.format, None, ui, query.answers)
    else:
        file = Path(output)
        directory = file.parent
        formats = choose_formats(known, options.format, file, ui, query.answers)

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


def _save_answer(text: str) -> str:
    """Parse the answer to whether to save: ``y``, ``n`` or ``o`` (another path)."""
    answer = text.strip().lower()
    for choice in ("yes", "no", "other"):
        if answer in (choice, choice[0]):
            return choice[0]
    raise ValueError(
        "Answer y to save it there, n not to save it, or o to enter another path."
    )


def _path(text: str) -> str:
    """Parse a path typed at a prompt, expanding ``~`` (and keeping a final ``/``)."""
    path = os.path.expanduser(text.strip())
    if not path:
        raise ValueError("Enter a file name, or a directory ending in /.")
    return path


def choose_formats(
    configured: Sequence[Format],
    cli_format: Format | None,
    file: Path | None,
    ui: UI,
    answers: Answers | None = None,
) -> tuple[Format, ...]:
    """Choose the output format(s).

    Parameters
    ----------
    configured : sequence of Format
        The formats from the template or ``[defaults]``.
    cli_format : Format or None
        ``--format``, which wins if given.
    file : Path or None
        The output file, whose extension is used next.
    ui : UI
        For asking, when nothing else decides.
    answers : Answers, optional
        Where to record the answer, if asked.

    Returns
    -------
    tuple of Format
        The formats.

    Raises
    ------
    InputRequired
        If nothing decides and the session isn't interactive.
    """
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
            Option("h5", Format.H5, "HDF5"),
            Option("mat", Format.MAT, "MATLAB (v7.3)"),
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
    """Decide how to save to a file, asking what to do if it exists.

    Parameters
    ----------
    path : Path
        The file.
    format : Format
        Its format.
    ui : UI
        For asking; non-interactive sessions save next to it instead.
    options : SaveOptions
        ``--overwrite`` and ``--append`` decide without asking.

    Returns
    -------
    Target
        Where to save.

    Raises
    ------
    Cancelled
        If the user cancels.
    """
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
    """Count a fetch's text values (each is a cell entry in a .mat file).

    Parameters
    ----------
    result : FetchResult
        The fetched data.

    Returns
    -------
    int
        The number of text values, including log sources.
    """
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
    """Warn before writing a lot of text to a .mat file.

    Text makes .mat files big and slow to load in MATLAB. Interactive users can
    continue, save as .h5 instead, skip the text items or cancel; others get a
    warning. Accepting (or ``allow``) is remembered in the file, so later appends
    don't ask again.

    Parameters
    ----------
    target : Target
        Where the data is to be saved.
    result : FetchResult
        The data.
    ui : UI
        For warnings and questions.
    threshold : int
        How many text values (including those already in the file) to allow
        without warning; 0 never warns.
    allow : bool
        ``--allow-large-text``.
    existing_entries : callable
        Counts the text values already in a file.
    accepted_before : callable
        Whether a file records that large text was accepted before.

    Returns
    -------
    tuple of Target and FetchResult
        Where to save, and what (without the text items if they're left out).

    Raises
    ------
    Cancelled
        If the user cancels.
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

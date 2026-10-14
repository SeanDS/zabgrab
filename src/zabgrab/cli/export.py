"""``zabgrab export``: writing a file's merged data to a new file."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import click

from ..adapters.formats import hdf5
from ..adapters.formats.delimited import write_delimited
from ..errors import ZabgrabError
from ..model.config import Format
from ..model.series import FetchResult
from ..services.metadata import export_metadata
from ..services.output import (
    STRUCTURED,
    SaveOptions,
    choose_formats,
    existing_target,
    guard_large_text,
)
from .app import AppContext, global_options, pass_app
from .reading import read_dataset, selection_options


@click.command("export")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "-o",
    "--output",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="File to write (.mat, .h5, .tsv or .csv).",
)
@click.option(
    "--format",
    "format_",
    type=click.Choice([f.value for f in Format]),
    help="Output format (default: from the output file name).",
)
@selection_options
@click.option("--overwrite", is_flag=True, help="Replace an existing output file.")
@click.option(
    "--allow-large-text",
    is_flag=True,
    help="Don't warn about writing lots of text to a .mat file.",
)
@global_options
@pass_app
def export_command(
    app: AppContext,
    file: Path,
    output: Path,
    format_: str | None,
    items: tuple[str, ...],
    from_: str | None,
    to: str | None,
    groups: tuple[str, ...],
    overwrite: bool,
    allow_large_text: bool,
) -> None:
    """Write the data in FILE (.mat or .h5) to a new file, as one group.

    The data of every fetch group is merged (repeated points are kept once), so
    this flattens a file that has been updated many times, or converts it to
    another format.
    """
    dataset, tz = read_dataset(
        app,
        file,
        verb=("export", "exported"),
        items=items,
        from_=from_,
        to=to,
        groups=groups,
    )
    defaults = app.config().config.defaults
    now = app.clock.now()
    resolutions = {series.resolution for series in dataset.series}
    if len(resolutions) > 1:
        raise ZabgrabError(
            f"{file} has both raw and hourly data.",
            hint="Export them separately, choosing groups with --group.",
        )
    if not dataset.series or not dataset.coverage:
        raise ZabgrabError("Nothing to export.", hint="Check --item, --from and --to.")

    options = SaveOptions(
        format=Format(format_) if format_ else None,
        overwrite=overwrite,
        allow_large_text=allow_large_text,
    )
    (format,) = choose_formats(
        [Format(defaults.format)] if isinstance(defaults.format, str) else [],
        options.format,
        output,
        app.ui,
    )
    target = existing_target(output, format, app.ui, options)
    result = FetchResult(series=dataset.series)
    target, result = guard_large_text(
        target,
        result,
        app.ui,
        threshold=defaults.mat_text_warn_entries or 0,
        allow=allow_large_text,
        existing_entries=hdf5.text_entries,
        accepted_before=hdf5.large_text_accepted,
    )
    filters: dict[str, Any] = {
        "items": list(items),
        "from": from_,
        "to": to,
        "groups": list(groups),
    }
    metadata = export_metadata(
        dataset, source=str(file), filters=filters, command=sys.argv, created=now
    )
    if target.format in STRUCTURED:
        hdf5.write_fetch(
            target.path,
            result,
            metadata,
            mat=target.format is Format.MAT,
            t_from=dataset.coverage[0][0],
            t_to=dataset.coverage[-1][1],
            large_text_accepted=target.large_text_accepted,
        )
    else:
        delimiter = "\t" if target.format is Format.TSV else ","
        write_delimited(target.path, result, metadata, delimiter=delimiter, tz=tz)

    count = len(result.series)
    app.ui.success(
        f"Exported {count} item{'' if count == 1 else 's'} ({result.points:,} points) "
        f"from {len(dataset.groups)} group{'' if len(dataset.groups) == 1 else 's'} "
        f"of {file} to {target.path}."
    )

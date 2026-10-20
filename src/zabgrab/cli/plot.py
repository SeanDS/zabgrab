"""``zabgrab plot``: plotting a file's merged data."""

from __future__ import annotations

from pathlib import Path

import click

from ..errors import ZabgrabError
from ..model.item import ItemSpec
from ..services.binning import Bins
from ..services.plotting import histogram_spec, plot_spec
from ..ui import plot
from .app import AppContext, global_options, pass_app
from .reading import bin_option, nothing_selected, read_dataset, selection_options


@click.command("plot")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@selection_options
@click.option(
    "-o",
    "--output",
    "outputs",
    multiple=True,
    type=click.Path(dir_okay=False, path_type=Path),
    help="Save the plot to this image file (e.g. .png, .pdf or .svg) instead of "
    "showing it. Repeat to save it to several files, e.g. in several formats.",
)
@click.option("--log-y", is_flag=True, help="Use log scales for the vertical axes.")
@bin_option
@global_options
@pass_app
def plot_command(
    app: AppContext,
    file: Path,
    items: tuple[ItemSpec, ...],
    match: tuple[str, ...],
    from_: str | None,
    to: str | None,
    groups: tuple[str, ...],
    outputs: tuple[Path, ...],
    log_y: bool,
    bins: Bins | None,
) -> None:
    """Plot the data in FILE (.mat or .h5).

    The data of every fetch group is merged, and lines only break over times that
    no fetch covered. Items share an axis when they have the same units. Hourly
    trends are drawn as the average, with the minimum and maximum as a band. Text
    items can't be plotted.

    With --bin, a histogram is drawn instead: how many of each item's values fall in
    each bin, with items side by side.

    Needs matplotlib: pip install 'zabgrab[plot]'.
    """
    dataset, tz = read_dataset(
        app,
        file,
        verb=("plot", "plotted"),
        items=items,
        match=match,
        from_=from_,
        to=to,
        groups=groups,
    )
    if not dataset.series:
        raise ZabgrabError("Nothing to plot.", hint=nothing_selected(file))
    spec = (
        histogram_spec(dataset.series, bins, app.ui, title=file.name, log_y=log_y)
        if bins is not None
        else plot_spec(
            dataset.series, dataset.coverage, app.ui, title=file.name, log_y=log_y
        )
    )
    plot.show_or_save(spec, tz, outputs)
    if outputs:
        app.ui.success(f"Saved the plot to {', '.join(map(str, outputs))}.")

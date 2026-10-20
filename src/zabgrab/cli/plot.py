"""``zabgrab plot``: plotting a file's merged data."""

from __future__ import annotations

from datetime import tzinfo
from pathlib import Path

import click

from ..adapters.dates import parse_time
from ..errors import ZabgrabError
from ..model.item import ItemSpec
from ..services.binning import Bins
from ..services.plotting import RefLine, histogram_spec, plot_spec
from ..ui import plot
from .app import AppContext, global_options, pass_app
from .reading import (
    bin_option,
    log_option,
    nothing_selected,
    read_dataset,
    selection_options,
    use_log,
)


def _hlines(
    ctx: click.Context, param: click.Parameter, value: tuple[str, ...]
) -> tuple[RefLine, ...]:
    """Read the ``--hline`` options."""
    try:
        return tuple(RefLine.parse(text) for text in value)
    except ValueError as exc:
        raise click.BadParameter(f"{exc}.") from None


def _vlines(
    app: AppContext, texts: tuple[str, ...], tz: tzinfo | None, *, values: bool
) -> list[RefLine]:
    """Read the ``--vline`` options: values for histograms, else times.

    A time can contain spaces, so it takes no units, and goes on every plot.
    """
    lines = []
    for text in texts:
        try:
            if values:
                lines.append(RefLine.parse(text))
                continue
            label, _, when = text.rpartition("=")
            moment = parse_time(when.strip(), now=app.clock.now(), tz=tz)
            lines.append(RefLine(label.strip() or when.strip(), moment.timestamp()))
        except ValueError as exc:
            raise click.BadParameter(f"{exc}.", param_hint="'--vline'") from None
    return lines


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
@log_option("Use log scales for the values (with --bin, for the counts).")
@bin_option
@click.option(
    "--hline",
    "hlines",
    multiple=True,
    metavar="[LABEL=]VALUE [UNITS]",
    callback=_hlines,
    help="Draw a horizontal reference line at VALUE, e.g. 'Max=500' or '500' "
    "(repeat for several). LABEL names it in the legend (by default, the value). "
    "With UNITS, e.g. 'Max=26 °C', it's drawn only on plots of items with those "
    "units; otherwise on every plot. With --bin, VALUE is a count, drawn through "
    "the bars.",
)
@click.option(
    "--vline",
    "vlines",
    multiple=True,
    metavar="[LABEL=]TIME|VALUE",
    help="Draw a vertical reference line (repeat for several): at a TIME, as for "
    "--from, e.g. 'Maintenance=2026-10-14 09:00'; or with --bin, at a VALUE among "
    "the bins, with optional UNITS as for --hline, e.g. 'Limit=1000 #/m3'.",
)
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
    log: bool,
    log_y: bool,
    bins: Bins | None,
    hlines: tuple[RefLine, ...],
    vlines: tuple[str, ...],
) -> None:
    """Plot the data in FILE (.mat or .h5).

    The data of every fetch group is merged, and lines only break over times that
    no fetch covered. Items share an axis when they have the same units. Hourly
    trends are drawn as the average, with the minimum and maximum as a band. Text
    items can't be plotted.

    With --bin, a histogram is drawn instead: how many of each item's values fall in
    each bin, with the bins up the side and items side by side. --log then puts
    the counts on a log scale.

    --hline and --vline draw reference lines, such as limits: horizontal ones at
    values (with --bin, counts) and vertical ones at times (with --bin, values).

    Needs matplotlib: pip install 'zabgrab[plot]'.
    """
    log = use_log(app, log, log_y)
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
    vertical = _vlines(app, vlines, tz, values=bins is not None)
    spec = (
        histogram_spec(
            dataset.series,
            bins,
            app.ui,
            title=file.name,
            log_y=log,
            hlines=hlines,
            vlines=vertical,
        )
        if bins is not None
        else plot_spec(
            dataset.series,
            dataset.coverage,
            app.ui,
            title=file.name,
            log_y=log,
            hlines=hlines,
            vlines=vertical,
        )
    )
    plot.show_or_save(spec, tz, outputs)
    if outputs:
        app.ui.success(f"Saved the plot to {', '.join(map(str, outputs))}.")

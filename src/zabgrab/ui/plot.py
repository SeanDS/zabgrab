"""Drawing plots with matplotlib (the optional ``plot`` extra).

matplotlib is imported only when a plot is drawn, so zabgrab works without it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ..errors import ZabgrabError
from ..services.plotting import Axis, BarAxis, HistogramSpec, PlotSpec, Reference

if TYPE_CHECKING:
    from matplotlib.figure import Figure

INSTALL_HINT = "Install it with: pip install 'zabgrab[plot]'"


def _pyplot(*, interactive: bool) -> Any:
    """Import ``matplotlib.pyplot``.

    Parameters
    ----------
    interactive : bool
        Whether a window will be shown. If not, the non-interactive Agg backend is
        used, so saving works without a display.

    Raises
    ------
    ZabgrabError
        If matplotlib isn't installed.
    """
    try:
        import matplotlib
    except ImportError:
        raise ZabgrabError("Plotting needs matplotlib.", hint=INSTALL_HINT) from None
    if not interactive:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    return plt


def draw(spec: PlotSpec, tz: tzinfo | None, *, interactive: bool = False) -> Figure:
    """Draw a plot.

    Parameters
    ----------
    spec : PlotSpec
        What to draw (see :func:`zabgrab.services.plotting.plot_spec`).
    tz : tzinfo or None
        The time zone to show times in (``None`` for local time).
    interactive : bool, optional
        Whether the figure will be shown in a window.

    Returns
    -------
    matplotlib.figure.Figure
        One subplot per axis in ``spec``, sharing the time axis.
    """
    plt = _pyplot(interactive=interactive)
    figure, subplots = plt.subplots(
        len(spec.axes),
        1,
        sharex=True,
        squeeze=False,
        figsize=(10, 2.5 + 2.5 * len(spec.axes)),
        layout="constrained",
    )
    colours = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    styles = _reference_styles(spec.axes)
    for axis, (subplot,) in zip(spec.axes, subplots, strict=True):
        for number, line in enumerate(axis.lines):
            colour = colours[number % len(colours)]
            for index, segment in enumerate(line.segments):
                times = [_wall_clock(t, tz) for t in segment.times]
                subplot.plot(
                    times,
                    segment.values,
                    color=colour,
                    # One legend entry per line, not per segment.
                    label=line.label if index == 0 else None,
                    marker="." if len(times) == 1 else None,
                )
                if segment.low is None or segment.high is None:
                    continue
                if len(times) == 1:
                    # A band needs two points; show a lone one's range as a bar.
                    (value,) = segment.values
                    subplot.errorbar(
                        times,
                        segment.values,
                        yerr=[[value - segment.low[0]], [segment.high[0] - value]],
                        color=colour,
                        alpha=0.5,
                    )
                else:
                    subplot.fill_between(
                        times,
                        segment.low,
                        segment.high,
                        color=colour,
                        alpha=0.2,
                        linewidth=0,
                    )
        subplot.set_ylabel(axis.units)
        if spec.log_y:
            # Values of zero or less can't be shown; leave them out of the lines.
            subplot.set_yscale("log", nonpositive="mask")
        subplot.grid(alpha=0.3)
        _references(
            subplot,
            axis.hlines,
            axis.vlines,
            styles=styles,
            across=lambda t: _wall_clock(t, tz),
        )
        subplot.legend(loc="best", fontsize="small")
    subplots[-1][0].set_xlabel(f"Time ({_zone_name(tz)})")
    if spec.title:
        figure.suptitle(spec.title)
    return cast("Figure", figure)


def draw_histogram(spec: HistogramSpec, *, interactive: bool = False) -> Figure:
    """Draw a histogram: bins along the bottom, counts up the side.

    The bins are drawn the same width, labelled with their ranges, lowest on the
    left: the first and last are open-ended, and the others' real widths can differ
    by orders of magnitude. Each series' bars sit side by side in each bin. Values
    run along the bottom, so vertical reference lines are drawn across the bins at
    values, and horizontal ones through the bars at counts.

    Parameters
    ----------
    spec : HistogramSpec
        What to draw (see :func:`zabgrab.services.plotting.histogram_spec`).
    interactive : bool, optional
        Whether the figure will be shown in a window.

    Returns
    -------
    matplotlib.figure.Figure
        One subplot per axis in ``spec``.
    """
    plt = _pyplot(interactive=interactive)
    figure, subplots = plt.subplots(
        len(spec.axes),
        1,
        squeeze=False,
        figsize=(10, 2.5 + 2.5 * len(spec.axes)),
        layout="constrained",
    )
    colours = plt.rcParams["axes.prop_cycle"].by_key()["color"]
    styles = _reference_styles(spec.axes)
    positions = range(len(spec.bins))
    for axis, (subplot,) in zip(spec.axes, subplots, strict=True):
        # The bars of a bin share 80% of its width, leaving a gap between bins.
        width = 0.8 / len(axis.bars)
        for number, bars in enumerate(axis.bars):
            offset = (number - (len(axis.bars) - 1) / 2) * width
            subplot.bar(
                [position + offset for position in positions],
                bars.counts,
                width,
                label=bars.label,
                color=colours[number % len(colours)],
            )
        subplot.set_ylabel("Count")
        if spec.log_y:
            # The log scale is for the counts; empty bins can't be shown on it.
            subplot.set_yscale("log", nonpositive="mask")
        subplot.grid(alpha=0.3, axis="y")
        subplot.set_xticks(list(positions), spec.bins)
        subplot.set_xlabel(f"Value [{axis.units}]" if axis.units else "Value")
        _references(subplot, axis.hlines, axis.vlines, styles=styles)
        subplot.legend(loc="best", fontsize="small")
    if spec.title:
        figure.suptitle(spec.title)
    return cast("Figure", figure)


#: Dash patterns for reference lines, so several are told apart in the legend.
REFERENCE_STYLES = ["--", ":", "-.", (0, (8, 3, 2, 3, 2, 3))]


def _reference_styles(axes: Sequence[Axis | BarAxis]) -> dict[str, Any]:
    """Give each reference line (by label) a dash pattern, the same on every plot."""
    labels = [line.label for axis in axes for line in (*axis.hlines, *axis.vlines)]
    return {
        label: REFERENCE_STYLES[number % len(REFERENCE_STYLES)]
        for number, label in enumerate(dict.fromkeys(labels))
    }


def _references(
    subplot: Any,
    hlines: Sequence[Reference],
    vlines: Sequence[Reference],
    *,
    styles: dict[str, Any],
    across: Callable[[float], Any] = lambda at: at,
) -> None:
    """Draw reference lines across a subplot, in a grey that data lines don't use.

    Horizontal lines go at their positions up the side, vertical ones at their
    positions along the bottom, converted by ``across`` (e.g. times to the
    wall-clock times the axis shows). ``styles`` gives each its dash pattern.
    """
    drawn = [(subplot.axhline, line.at, line.label) for line in hlines] + [
        (subplot.axvline, across(line.at), line.label) for line in vlines
    ]
    for draw_line, at, label in drawn:
        draw_line(
            at,
            color="0.25",
            linewidth=1.2,
            linestyle=styles[label],
            label=label,
        )


def show_or_save(
    spec: PlotSpec | HistogramSpec, tz: tzinfo | None, outputs: Sequence[Path] = ()
) -> None:
    """Show a plot in a window, or save it to files.

    Parameters
    ----------
    spec : PlotSpec or HistogramSpec
        What to draw: lines over time, or a histogram.
    tz : tzinfo or None
        The time zone to show times in (``None`` for local time).
    outputs : sequence of Path, optional
        The image files to save to (each one's extension picks its format, e.g.
        ``.png``, ``.pdf`` or ``.svg``). If none are given, the plot is shown in a
        window.

    Raises
    ------
    ZabgrabError
        If matplotlib isn't installed, or a file can't be written.
    """
    interactive = not outputs
    figure = (
        draw_histogram(spec, interactive=interactive)
        if isinstance(spec, HistogramSpec)
        else draw(spec, tz, interactive=interactive)
    )
    plt = _pyplot(interactive=interactive)
    try:
        if interactive:
            plt.show()
        # The figure is drawn once and saved in each format.
        for output in outputs:
            try:
                figure.savefig(output)
            except (OSError, ValueError) as exc:
                raise ZabgrabError(
                    f"Can't save the plot to {output}.", details=[str(exc)]
                ) from exc
    finally:
        plt.close(figure)


def _wall_clock(time: float, tz: tzinfo | None) -> datetime:
    """Convert POSIX seconds to a naive wall-clock time in ``tz``.

    matplotlib shows naive datetimes as they are, so converting each time
    separately keeps the axis right across daylight saving changes (a single fixed
    offset wouldn't).
    """
    return datetime.fromtimestamp(time, tz).replace(tzinfo=None)


def _zone_name(tz: tzinfo | None) -> str:
    """Name a timezone for the time axis's label."""
    return "local time" if tz is None else str(tz)

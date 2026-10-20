"""Counting values in bins (``show --bin`` and ``plot --bin``).

Bins are given by their edges, in increasing order. N edges make N + 1 bins: the
first holds everything below the first edge and the last everything from the last
edge up, and each bin includes its lower edge but not its upper one, as
``numpy.histogram``'s bins do: with edges 10 and 100, 10 is in ``10 to 100`` and
100 in ``≥ 100``.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from ..model.series import Series
from .listing import ListingOptions, matching, value_column


@dataclass(frozen=True)
class Bins:
    """Bins, by their edges.

    Attributes
    ----------
    edges : tuple of float
        The edges, strictly increasing.

    Examples
    --------
    >>> bins = Bins.parse(["10,100", "1000"])
    >>> bins.labels
    ['< 10', '10 to 100', '100 to 1000', '≥ 1000']
    >>> [bins.index(value) for value in (5, 10, 99.9, 100, 5000)]
    [0, 1, 1, 2, 3]
    """

    edges: tuple[float, ...]

    @classmethod
    def parse(cls, texts: Sequence[str]) -> Bins:
        """Read bin edges given on the command line.

        Parameters
        ----------
        texts : sequence of str
            Comma-separated numbers; each ``--bin`` given adds its edges.

        Returns
        -------
        Bins
            The bins.

        Raises
        ------
        ValueError
            If an edge isn't a finite number, or the edges aren't increasing.
        """
        edges = []
        for part in (part for text in texts for part in text.split(",")):
            try:
                edge = float(part)
            except ValueError:
                raise ValueError(f"{part.strip()!r} isn't a number") from None
            if not math.isfinite(edge):
                raise ValueError(f"{part.strip()!r} isn't a finite number")
            edges.append(edge)
        if any(low >= high for low, high in pairwise(edges)):
            raise ValueError("the edges must increase, e.g. 10,100,1000")
        return cls(tuple(edges))

    @property
    def labels(self) -> list[str]:
        """Name the bins, e.g. ``< 10``, ``10 to 100`` and ``≥ 100``."""
        names = [_number(edge) for edge in self.edges]
        middle = [f"{low} to {high}" for low, high in pairwise(names)]
        return [f"< {names[0]}", *middle, f"≥ {names[-1]}"]

    def index(self, value: float) -> int:
        """Find the bin a value falls in (its lower edge counts as in it)."""
        return bisect.bisect_right(self.edges, value)


@dataclass(frozen=True)
class Histogram:
    """How many of a series' values fall in each bin.

    Attributes
    ----------
    series : Series
        The series.
    counts : list of int
        The number of values in each bin.
    """

    series: Series
    counts: list[int]

    @property
    def total(self) -> int:
        """The number of values counted."""
        return sum(self.counts)


def histograms(
    series: Sequence[Series], bins: Bins, options: ListingOptions | None = None
) -> list[Histogram]:
    """Count each numeric series' values in bins.

    Raw data's values are counted, and hourly trends' averages. Text items have no
    size, so they're left out.

    Parameters
    ----------
    series : sequence of Series
        The data.
    bins : Bins
        The bins.
    options : ListingOptions, optional
        Filters to apply first (``--grep``, ``--above`` and ``--below``).

    Returns
    -------
    list of Histogram
        One per numeric series, in the order given.
    """
    options = options or ListingOptions()
    result = []
    for one in series:
        if not one.item.value_type.numeric:
            continue
        values = one.columns[value_column(one, options.sort)]
        counts = [0] * (len(bins.edges) + 1)
        for index in matching(one, options):
            counts[bins.index(values[index])] += 1
        result.append(Histogram(one, counts))
    return result


def _number(value: float) -> str:
    """Write an edge as given: ``10`` rather than ``10.0``."""
    return str(int(value)) if value.is_integer() else repr(value)

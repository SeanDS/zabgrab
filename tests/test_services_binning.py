"""Counting values in bins."""

from __future__ import annotations

import re

import pytest

from zabgrab.services.binning import Bins, histograms
from zabgrab.services.listing import ListingOptions

from .test_services_listing import TEXT, hourly, raw


class TestBins:
    def test_parse(self) -> None:
        assert Bins.parse(["10, 100", "1e3"]).edges == (10, 100, 1000)

    @pytest.mark.parametrize(
        ("texts", "message"),
        [
            (["10,x"], "'x' isn't a number"),
            (["10,"], "'' isn't a number"),
            (["inf"], "'inf' isn't a finite number"),
            (["10,10"], "the edges must increase"),
            (["100", "10"], "the edges must increase"),
        ],
    )
    def test_invalid(self, texts: list[str], message: str) -> None:
        with pytest.raises(ValueError, match=re.escape(message)):
            Bins.parse(texts)

    def test_labels(self) -> None:
        assert Bins((0.5,)).labels == ["< 0.5", "≥ 0.5"]
        assert Bins((-1, 2.5, 7)).labels == ["< -1", "-1 to 2.5", "2.5 to 7", "≥ 7"]

    def test_lower_edges_are_inside(self) -> None:
        # As numpy.histogram: [10, 100) and [100, ∞).
        bins = Bins((10, 100))
        assert [bins.index(v) for v in (9.99, 10, 99.99, 100, 1e9)] == [0, 1, 1, 2, 2]


def test_histograms() -> None:
    a = raw(1, [1, 2, 3, 4, 5], [5.0, 10.0, 50.0, 100.0, 500.0])
    (histogram,) = histograms([a, TEXT], Bins((10, 100)))
    assert histogram.series is a
    assert histogram.counts == [1, 2, 2]
    assert histogram.total == 5


def test_filters_apply_first() -> None:
    a = raw(1, [1, 2, 3], [5.0, 50.0, 500.0])
    (histogram,) = histograms([a], Bins((10, 100)), ListingOptions(below=100))
    assert histogram.counts == [1, 1, 0]


def test_hourly_averages() -> None:
    stats = hourly({"min": [1, 2], "avg": [3, 20], "max": [80, 90]})
    (histogram,) = histograms([stats], Bins((10,)))
    assert histogram.counts == [1, 1]

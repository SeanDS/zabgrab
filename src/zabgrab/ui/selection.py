"""Parsing of multiple-choice answers such as ``1,3,5-8`` or ``all``."""

from __future__ import annotations


def parse_selection(text: str, count: int) -> list[int]:
    """Parse a selection of 1-based option numbers into 0-based indices.

    Accepts comma-separated numbers and inclusive ranges (``1,3,5-8``), or ``all``.
    Indices are returned in the order given, without duplicates.

    Raises
    ------
    ValueError
        With a message suitable for showing to the user.
    """
    text = text.strip().lower()
    if not text:
        raise ValueError("Enter at least one number, e.g. '1' or '1,3,5-8', or 'all'.")
    if text == "all":
        return list(range(count))

    indices: list[int] = []
    for part in text.split(","):
        part = part.strip()
        start_text, dash, end_text = part.partition("-")
        try:
            start = int(start_text)
            end = int(end_text) if dash else start
        except ValueError:
            raise ValueError(
                f"{part!r} isn't a number or range; use e.g. '1,3,5-8' or 'all'."
            ) from None

        if start > end:
            raise ValueError(
                f"Range {part!r} is backwards; did you mean {end}-{start}?"
            )
        for number in (start, end):
            if not 1 <= number <= count:
                raise ValueError(f"{number} is out of range; choose from 1 to {count}.")

        for index in range(start - 1, end):
            if index not in indices:
                indices.append(index)

    return indices

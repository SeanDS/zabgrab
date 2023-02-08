"""Utilities."""

import locale
import warnings
import dateparser


def parse_date(datestr, base=None):
    """Human date parser."""

    settings = {"DATE_ORDER": "DMY", "RELATIVE_BASE": base}

    # There is no default DATE_ORDER setting in English locales; in such cases
    # dateparser unfortunately defaults to MDY date order which only applies to one
    # particular English locale (see
    # https://en.wikipedia.org/wiki/Date_format_by_country). We set the default to DMY,
    # and only revert to MDY if the locale is en_US.
    lang, _ = locale.getlocale()
    if lang == "en_US":
        settings = {"DATE_ORDER": "MDY"}

    # https://github.com/scrapinghub/dateparser/issues/1089
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=(
                "The localize method is no longer necessary, as this time zone "
                "supports the fold attribute"
            ),
        )

        date = dateparser.parse(datestr, settings=settings)

    if date is None:
        raise ValueError(f"Unable to parse {repr(datestr)}.")

    return date

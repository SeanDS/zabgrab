"""Zabgrab command line interface"""

import sys
import logging
from datetime import datetime
from functools import partial
from collections import defaultdict
from pathlib import Path
import numpy as np
from scipy.io import savemat
import click
import tomli
from pyzabbix import ZabbixAPI, ZabbixAPIException
from . import __version__, PROGRAM
from .utilities import parse_date

# Configure logging to stderr.
logging.basicConfig()


class _ZabgrabHandler:
    """Manages CLI state."""

    def __init__(self):
        self.debug = None
        self.config_path = None
        self.user_config_path = Path(click.get_app_dir(PROGRAM)) / f"{PROGRAM}.conf"
        self._config_dict = None
        self.now = datetime.utcnow()
        self._verbosity = logging.WARNING
        self._zabbix_api = None

    @property
    def logpath(self):
        # Use the root logger if debugging is enabled.
        return None if self.debug else __package__

    @property
    def verbosity(self):
        """Verbosity on stdout."""
        return self._verbosity

    @verbosity.setter
    def verbosity(self, verbosity):
        verbosity = self._verbosity - 10 * int(verbosity)
        verbosity = min(max(verbosity, logging.DEBUG), logging.CRITICAL)
        self._verbosity = verbosity
        # Set the logger's level.
        logging.getLogger(self.logpath).setLevel(self._verbosity)

    @property
    def verbose(self):
        """Verbose output enabled.

        Returns
        -------
        True if the verbosity is enough for WARNING (and lower) messages to be
        displayed; False otherwise.
        """
        return self.verbosity <= logging.WARNING

    @property
    def _config(self):
        if self._config_dict is None:
            config_path = None

            if self.config_path is not None:
                self.echo_debug(
                    f"Using config path specified by user: {self.config_path}"
                )
                config_path = Path(self.config_path)
            elif self.user_config_path is not None and self.user_config_path.is_file():
                self.echo_debug(
                    f"Using default user config path: {self.user_config_path}"
                )
                config_path = self.user_config_path
            else:
                self.echo_warning("No config path set; proceeding with defaults.")

            if config_path is not None:
                with config_path.open("rb") as fobj:
                    try:
                        self._config_dict = tomli.load(fobj)
                    except tomli.TOMLDecodeError as err:
                        self.echo_exception(err, exit_=True)
            else:
                # Empty config; have to rely on user flags.
                self._config_dict = {}

        return self._config_dict

    def config(self, *path, _config=None):
        """Retrieve value corresponding to (possibly hierarchical) key `path` from
        config file."""
        if _config is None:
            _config = self._config

        top, *rest = path

        try:
            value = _config[top]
        except KeyError:
            return None

        if rest:
            return self.config(*rest, _config=_config[top])

        return value

    def set_config(self, key, value):
        """Set a config value using a string path representation."""

        def setrecursive(mapping, attrs, value):
            key = attrs.pop(0)

            if key not in mapping:
                mapping[key] = {}

            if attrs:
                setrecursive(mapping[key], attrs, value)
            else:
                if mapping[key]:
                    self.echo_debug(
                        f"Overwriting existing config value for key {repr(key)}"
                    )

                mapping[key] = value

        setrecursive(self._config, key.split("."), value)

    def template(self, template):
        """Retrieve template settings from config."""
        return self.config("template", template)

    def _echo(self, *args, err=False, exit_=False, **kwargs):
        click.echo(*args, err=err, **kwargs)

        if exit_:
            code = 1 if err else 0
            sys.exit(code)

    def echo(self, *args, **kwargs):
        if self.verbosity > logging.WARNING:
            return

        self._echo(*args, **kwargs)

    def echo_info(self, msg, *args, **kwargs):
        if self.verbosity > logging.INFO:
            return

        msg = click.style(msg, fg="blue")
        self._echo(msg, *args, **kwargs)

    def echo_error(self, msg, *args, **kwargs):
        if self.verbosity > logging.ERROR and not self.debug:
            return

        msg = click.style(msg, fg="red")
        self._echo(msg, *args, err=True, **kwargs)

    def echo_exception(self, exception, *args, **kwargs):
        # Assume the message is the first argument.
        msg = str(exception.args[0])

        if not self.debug:
            # Just echo the error string, not the traceback.
            return self.echo_error(msg, *args, **kwargs)

        import traceback

        should_exit = kwargs.pop("exit_", False)

        tb = "".join(traceback.format_tb(exception.__traceback__))
        self._echo(tb, *args, err=True, exit_=False, **kwargs)
        msg = click.style(msg, fg="red")
        self._echo(msg, *args, err=True, exit_=should_exit, **kwargs)

    def echo_warning(self, msg, *args, **kwargs):
        if self.verbosity > logging.WARNING:
            return

        msg = click.style(msg, fg="yellow")
        self._echo(msg, *args, **kwargs)

    def echo_debug(self, *args, **kwargs):
        if self.verbosity > logging.DEBUG:
            return

        self._echo(*args, **kwargs)

    def echo_key(self, key, separator=True, nl=True):
        key = click.style(key, fg="green")
        if separator:
            key = f"{key}: "
        self.echo(key, nl=nl)

    def echo_key_value(self, key, value):
        self.echo_key(key, separator=True, nl=False)
        self.echo(value)

    @property
    def zabbix_host(self):
        host = self.config("server", "host")
        if host is None:
            raise ValueError("No server host specified.")
        return host

    @property
    def zabbix_api_token(self):
        token = self.config("server", "api_token")
        if token is None:
            raise ValueError("No server API token specified.")
        return token

    @property
    def zabbix_api(self):
        if self._zabbix_api is None:
            self._zabbix_api = ZabbixAPI(self.zabbix_host)

            try:
                self._zabbix_api.login(api_token=self.zabbix_api_token)
            except ZabbixAPIException as exc:
                # Make the error message a bit nicer.
                msg = f"Error logging in to Zabbix API: {exc.args[0]}"
                raise ZabbixAPIException(msg, *exc.args[1:]) from exc

        return self._zabbix_api

    def fetch_zabbix_history(self, itemids, **kwargs):
        try:
            data = self.zabbix_api.history.get(itemids=itemids, **kwargs)
        except ZabbixAPIException as exc:
            # Make the error message a bit nicer.
            msg = f"Error fetching data from Zabbix API: {exc.args[0]}"
            raise ZabbixAPIException(msg, *exc.args[1:]) from exc

        # Regroup data into individual itemid arrays.
        timeseries = defaultdict(list)

        for row in data:
            itemid = row["itemid"]  # NOTE: keep as str for mat file compatibility.
            timeseries[itemid].append(
                (datetime.fromtimestamp(int(row["clock"])), float(row["value"]))
            )

        out = dict()
        for key in list(timeseries):
            out[key] = np.array(timeseries[key])

        return out


def _set_state_flag(ctx, _, value, *, flag):
    """Set state flag."""
    handler = ctx.ensure_object(_ZabgrabHandler)
    setattr(handler, flag, value)


def _set_verbosity(ctx, param, value):
    """Set state verbosity."""
    handler = ctx.ensure_object(_ZabgrabHandler)

    # Quiet verbosity is negative.
    if param.name == "quiet":
        value = -value

    handler.verbosity = value


def _set_config_path(ctx, _, value):
    handler = ctx.ensure_object(_ZabgrabHandler)

    if value is not None:
        handler.config_path = value


def _set_config_value(ctx, _, values):
    handler = ctx.ensure_object(_ZabgrabHandler)

    if values is not None:
        for value in values:
            key, value = value.split("=", maxsplit=1)
            handler.set_config(key, value)


def _edit_config(ctx, _, value):
    handler = ctx.ensure_object(_ZabgrabHandler)

    if value:
        filename = handler.user_config_path
        filename.parent.mkdir(parents=True, exist_ok=True)

        # Edit and update the config.
        handler.echo(f"Opening user config at {filename}...")
        click.edit(filename=filename)
        handler.echo("Saved new config successfully.")

        ctx.exit()


def _list_templates(ctx, _, value):
    handler = ctx.ensure_object(_ZabgrabHandler)

    if value:
        templates = handler.config("template")

        if templates:
            handler.echo("Templates found in configuration:")
            for template in templates:
                handler.echo(f"- {template}")
        else:
            handler.echo("No templates configured.")

        ctx.exit()


class DateParamType(click.ParamType):
    """Date parameter support for Click."""

    name = "date"

    def convert(self, value, param, ctx):
        if value is None:
            return None

        handler = ctx.ensure_object(_ZabgrabHandler)

        try:
            return parse_date(value, base=handler.now)
        except ValueError:
            self.fail(f"{repr(value)} is not a valid date", param, ctx)


verbose_option = click.option(
    "-v",
    "--verbose",
    count=True,
    callback=_set_verbosity,
    expose_value=False,
    is_eager=True,
    help="Increase verbosity (can be specified multiple times).",
)
quiet_option = click.option(
    "-q",
    "--quiet",
    count=True,
    callback=_set_verbosity,
    expose_value=False,
    help="Decrease verbosity (can be specified multiple times).",
)
debug_option = click.option(
    "--debug",
    is_flag=True,
    default=False,
    callback=partial(_set_state_flag, flag="debug"),
    expose_value=False,
    is_eager=True,
    help=(
        f"Show full exceptions when errors are encountered, and display all (not just "
        f"those of {PROGRAM}) logs when -v is specified."
    ),
)
version_option = click.version_option(version=__version__, prog_name=PROGRAM)


@click.command()
@click.option(
    "-t",
    "--template",
    type=str,
    help=(
        "Template to use. Items defined by the template are retrieved in addition to "
        "others explicitly defined via flags."
    ),
)
@click.option(
    "--item",
    "items",
    type=(click.IntRange(min=0), click.Choice(("0", "1", "2", "3", "4"))),
    multiple=True,
    help=(
        "Item ID and history type to retrieve, separated by whitespace; e.g. "
        "'12345 3'. Supported history types are: 0 = numeric float; 1 = character; 2 = "
        "log; 3 = numeric unsigned; 4 = text."
    ),
)
@click.option(
    "--from",
    "start",
    type=DateParamType(),
    default="1 hour ago",
    show_default=True,
    help=(
        "Start date. Most standard datetime formats as well as natural language are "
        "supported."
    ),
)
@click.option(
    "--to",
    "end",
    type=DateParamType(),
    default="now",
    show_default=True,
    help=(
        "End date. Most standard datetime formats as well as natural language are "
        "supported."
    ),
)
@click.option(
    "--print/--no-print",
    "print_",
    is_flag=True,
    default=False,
    help="Print data to stdout.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(
        file_okay=True,
        dir_okay=False,
        writable=True,
        resolve_path=True,
        path_type=Path,
    ),
    help=(
        "Path to save the retrieved data to. Supported extensions are: 'mat' = "
        "MATLAB; 'txt' (or anything else) = tab separated values."
    ),
)
@click.option(
    "-c",
    "--config",
    type=click.File(mode="r"),
    envvar="ZABGRAB_CONFIG",
    callback=_set_config_path,
    expose_value=False,
    help=(
        "Path to zabgrab config file. If not specified, the environment variable "
        "ZABGRAB_CONFIG is used, otherwise the system default configuration directory."
    ),
)
@click.option(
    "-o",
    "--option",
    type=str,
    callback=_set_config_value,
    expose_value=False,
    multiple=True,
    help=(
        "Set configuration key in the form 'key1.key2=value', e.g. "
        "'server.host=http://example.org'."
    ),
)
@click.option(
    "--edit-config",
    is_flag=True,
    callback=_edit_config,
    expose_value=False,
    is_eager=True,
    help="Open the user configuration file for editing.",
)
@click.option(
    "--list-templates",
    is_flag=True,
    callback=_list_templates,
    expose_value=False,
    is_eager=True,
    help="List configured templates then exit.",
)
@verbose_option
@quiet_option
@debug_option
@version_option
@click.pass_context
def zabgrab(ctx, template, items, start, end, print_, output_path):
    """Retrieve timeseries data from Zabbix API.

    Sean Leavey <sean.leavey@stfc.ac.uk>
    """
    handler = ctx.ensure_object(_ZabgrabHandler)

    # If a template is passed in, it might add to the items passed in, so we need to
    # modify the tuple.
    items = list(items)

    if start > end:
        handler.echo_warning(
            f"Specified from date ({start}) is later than to date ({end}) - swapping."
        )
        start, end = end, start

    handler.echo_debug(f"Data will be fetched between {start} and {end}")

    if template:
        template_data = handler.template(template)
        handler.echo_debug(
            f"Using template {repr(template)} with data {repr(template_data)}"
        )
        try:
            itemids = template_data["itemids"]
            history = template_data["history"]
        except KeyError:
            handler.echo_error(
                "Template must define both 'itemids' and 'history' keys", exit_=True
            )

        items.extend((str(itemid), str(history)) for itemid in itemids)

    if items:
        data = {}

        for itemid, history in items:
            handler.echo_debug(f"Fetching itemid {itemid}")

            # Whilst itemids are numeric, they are treated as strings in keys, comms,
            # etc.
            itemid = str(itemid)

            try:
                _data = handler.fetch_zabbix_history(
                    itemids=[itemid],
                    output="extend",
                    time_from=round(start.timestamp()),
                    time_till=round(end.timestamp()),
                    history=history,
                )
            except Exception as exc:
                handler.echo_exception(exc)
            else:
                try:
                    data[itemid] = _data[itemid]
                except KeyError:
                    handler.echo_error(
                        f"Server did not return data for itemid {itemid}. Check that "
                        f"the itemid, history and from/to values are appropriate for "
                        f"the request."
                    )

        if print_:
            for itemid, timeseries in data.items():
                handler.echo_key_value("itemid", itemid)
                for row in timeseries:
                    handler.echo_key_value(row[0].isoformat(), row[1])

        if output_path is not None:
            # Save to a file.
            handler.echo(f"Saving data to {output_path.resolve()}...")

            if output_path.suffix == ".mat":
                # Convert datetimes to strings.
                out = {}
                for key, timeseries in data.items():
                    # Make data MATLAB compatble.
                    key = f"item_{key}"
                    for row in timeseries:
                        row[0] = row[0].isoformat()

                    out[key] = timeseries

                savemat(output_path, out)
            else:
                # Tab separated values.
                with output_path.open("w") as fobj:
                    for key, timeseries in data.items():
                        fobj.write(f"itemid\t{key}\n")
                        for row in timeseries:
                            line = "\t".join([str(col) for col in row])
                            fobj.write(f"{line}\n")
    else:
        handler.echo("No items specified.")


if __name__ == "__main__":
    zabgrab()

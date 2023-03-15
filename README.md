# Zabgrab
Command line tool for Zabbix data retrieval.

## Installation
Zabgrab can be installed using your favourite Python package manager, e.g. `pip`:

```bash
$ pip install zabgrab
```

This should automatically install the latest version of Zabgrab and its dependencies.
There should then be a `zabgrab` command available in your terminal or console.

Zabgrab can be updated later by appending the `--upgrade` option to the `pip install`
command above. The current version can be checked with `zabgrab --version`.

## Usage
A common use for Zabgrab is to retrieve a timeseries between a start and end time from
a remote server. This can be done with e.g.

```bash
$ zabgrab -o server.host="http://zabbix.example.org/" -o server.api_token="xyz" --itemid 123 0 --from "1 hour ago" --to "now"
```

The `-o` flags set configuration options. At least `server.host` and `server.api_token`
are required if Zabgrab cannot find a local user configuration file (see below). The
`--itemid` option tells Zabgrab to retrieve the Zabbix item; it requires two values, the
Zabbix item ID and the history type (see below). The `--from` and `--to` options specify
the dates between which to retrieve data, and support natural language queries as well
as standard date formats.

The data can be output as text to the terminal by specifying `--print`. It can also
be saved to file by specifying `--output` followed by a path. The following extensions
are supported:

- `.mat`: MATLAB mat
- `.txt` (or anything else): tab separated values

Type `zabgrab --help` for full usage details.

### History types
Zabbix has various internal data types. The correct data type must be specified in calls
to the API via Zabgrab. The [Zabbix API
documentation](https://www.zabbix.com/documentation/current/en/manual/api/reference/history/get)
lists the available data types. Most timeseries are either "0" (numerical float) or "3"
(numeric unsigned).

## Configuration
Zabgrab supports configuration files to define common options such as the Zabbix server
and API key. It can also be used to specify templates, which are groups of items with
an associated name that can be retrieved via Zabgrab using the `--template` (or `-t`)
option.

The configuration file is stored in a file called `zabgrab.conf` in a directory called
`zabgrab` in the operating system specific user configuration directory. For example, on
Linux this file should be located at `~/.config/zabgrab/zabgrab.conf` and on Windows at
`C:\Users\<user>\AppData\Roaming\zabgrab\zabgrab.conf`.

The location of the configuration file can be overridden using the environment variable
`ZABGRAB_CONFIG`.

The configuration is expected to be in [TOML](https://toml.io/) format.

## Credits
Sean Leavey <sean.leavey@stfc.ac.uk>

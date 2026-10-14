# Zabgrab
Command line tool for Zabbix data retrieval.

> **Zabgrab is being rewritten for version 1.0.0.** See [PLAN.md](PLAN.md) for the
> design. Configuration, finding hosts/items and fetching data as TSV/CSV work so
> far; MATLAB/HDF5 output comes next. The full documentation will be written before
> the release.

## Installation
Requires Python 3.11 or later, and a Zabbix 7.0 or later server.

```bash
pip install zabgrab
```

For development:

```bash
pip install -e ".[dev]"
pre-commit install
```

## Configuration
Configuration is in [TOML](https://toml.io/). Zabgrab reads these files, with later
files overriding earlier ones:

1. system: `/etc/zabgrab/config.toml` (Windows: `%PROGRAMDATA%\zabgrab\config.toml`)
2. user: `$XDG_CONFIG_HOME/zabgrab/config.toml`, by default
   `~/.config/zabgrab/config.toml` on Linux and macOS (Windows:
   `%APPDATA%\zabgrab\config.toml`)
3. project: the nearest `zabgrab.toml` in the current directory or its parents (up to,
   but not including, your home directory)
4. `--config PATH` or `$ZABGRAB_CONFIG`

`--isolated` ignores files 1-3.

Useful commands:

- `zabgrab config paths`: which files are read, and which exist
- `zabgrab config show [--sources]`: the merged configuration, optionally showing
  which file each value came from
- `zabgrab config edit [--target user|project|system]`: edit a config file (created if
  needed), and check it's valid afterwards

### Servers and logging in
Define servers in your config:

```toml
[servers.demo]
url = "https://zabbix.example.org"   # the Zabbix web frontend's URL
token_env = "DEMO_ZABBIX_TOKEN"      # optional: environment variable holding an API token
# auth = "password"                  # log in with username and password instead
# username = "me"                    # default username for password login
# verify_ssl = false                 # for self-signed certificates (insecure)
# timeout = 30                       # seconds

[defaults]
server = "demo"                      # used when --server isn't given
```

Create API tokens in the Zabbix web UI under *User settings → API tokens*. Zabgrab
looks for a token in the server's `token_env` variable, then in your system keyring. If
there's none, it asks you to paste one (and offers to save it to the keyring) or to log
in with a username and password.

Manage tokens stored in the keyring with:

```bash
zabgrab token list [--check]         # servers and where their tokens come from
zabgrab token add demo               # asks for the token, checks it, stores it
printenv TOKEN | zabgrab token add demo -   # or a file: zabgrab token add demo token.txt
zabgrab token delete demo
zabgrab token reset                  # delete all of zabgrab's tokens
```

Tokens are kept in keyring entries under the service `zabgrab`, named `token1`,
`token2`, and so on (one per server).

Global options such as `-v`, `-q`, `--debug` and `--config` can be given before or
after the command: `zabgrab -v hosts` and `zabgrab hosts -v` are the same.

If no server is given or configured, zabgrab asks you to choose one, or to enter a URL
(which it can save to your config).

## Finding hosts and items

```bash
zabgrab hosts                       # all hosts
zabgrab hosts demo --group Lab      # hosts matching "demo" in groups matching "Lab"
zabgrab items "Particles *" --host demo1
zabgrab items --key "net.if.in[*]" --server demo --server lab
zabgrab items --item 12345 --item "host=demo1,key=system.uptime"
```

Patterns work like the filters in the Zabbix web UI: they're case-insensitive, match
anywhere in the text, and `*` matches any text.

## Fetching data

```bash
zabgrab fetch -t particles -o                       # a template, saved under an automatic name
zabgrab fetch -t particles -t temps -o data.csv     # stacked templates, in one file
zabgrab fetch -s demo -i "host=demo1,name=CPU *" --from "24 hours ago" --to now -o
zabgrab fetch -t particles --resolution 1h --agg avg --agg max -o   # Zabbix's hourly trends
zabgrab fetch -t particles --explain                 # show the settings and where they came from
zabgrab fetch -t particles --dry-run                 # show what would be fetched
```

Templates define what to fetch, and can set any `fetch` option:

```toml
[templates.particles]
server = "demo"                                     # or a URL, or a choice (see below)
items = [{ host = "demo1", name = "Particles *" }, { itemid = 12345 }]
from = "24 hours ago"
to = "now"
resolution = "raw"                                  # or "1h" for hourly trends
format = "csv"
```

Command line options override templates, which override `[defaults]`; zabgrab says
when an option overrides a template. Stacked templates must agree on the time range,
resolution and statistics. Anything missing (such as the time range) is asked for.

Times can be ISO 8601 (`2026-10-14 09:00`, `2026-10-14T09:00:00Z`), natural language
(`now`, `24 hours ago`, `yesterday`) or day-first dates (`14/10/2026`), in the
`timezone` setting (default: local time).

`-o`/`--output` takes a file name, a directory (ending in `/`), or no value for an
automatic name built from `output_pattern` (default
`{hosts}_{templates}_{from}--{to}`). The format comes from `--format`, the file
extension, or the `format` setting. Saved files start with `#` comment lines
containing metadata (as JSON) describing exactly what was fetched; numpy and pandas
skip them with `comment="#"`.

## Credits
Sean Leavey <sean.leavey@stfc.ac.uk>

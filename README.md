# Zabgrab
Command line tool for Zabbix data retrieval.

> **Zabgrab is being rewritten for version 1.0.0.** See [PLAN.md](PLAN.md) for the
> design. Configuration, finding hosts/items, fetching data (MATLAB, HDF5, TSV, CSV),
> saving fetches as templates, updating, exporting and plotting files work so far.
> The full documentation will be written before the release.

## Installation
Requires Python 3.11 or later, and a Zabbix 7.0 or later server.

```bash
pip install zabgrab
pip install 'zabgrab[plot]'    # with plotting (matplotlib)
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
zabgrab items --host demo1 --sort "last value"
```

Patterns work like the filters in the Zabbix web UI: they're case-insensitive, match
anywhere in the text, and `*` matches any text.

Items are listed by item ID. `--sort` takes any column header instead, in any case.
Last values sort numbers in numeric order, and ages put the newest first.

## Fetching data

```bash
zabgrab fetch -t particles -o                       # a template, saved under an automatic name
zabgrab fetch -t particles -i 12345 -o data.h5      # a template plus another item
zabgrab fetch -s demo -i "host=demo1,name=CPU *" --from "24 hours ago" --to now -o
zabgrab fetch -t particles --resolution 1h --agg avg --agg max -o   # Zabbix's hourly trends
zabgrab fetch -t particles --explain                 # show the settings and where they came from
zabgrab fetch -t particles --dry-run                 # show what would be fetched
```

`--agg` picks which of Zabbix's hourly statistics to save, so it only applies with
`--resolution 1h`. Raw data is saved as it is; an `--agg` given with raw data (or by
a template) is ignored with a warning.

Templates define what to fetch, and can set any `fetch` option:

```toml
[templates.particles]
server = "demo"                                     # or a URL, or { choices = ["demo", "lab"] }
items = [{ host = "demo1", name = "Particles *" }, { itemid = 12345 }]
from = "24 hours ago"
to = "now"
resolution = "raw"                                  # or "1h" for hourly trends
format = "h5"
```

Each fetch uses one template (or none) and one server. A template's server can be a
list of choices, which you're asked to pick from (`--server` picks one without
asking). Command line options override the template, which overrides `[defaults]`;
zabgrab says when an option overrides a template. Anything missing (such as the time
range or the format) is asked for,
and afterwards zabgrab shows the command that would have avoided the questions
(`Next time: zabgrab fetch ...`).

### Saving a fetch as a template

Add `--save-template NAME` to any fetch (including `--dry-run` and `--explain`) to
save it as a template, then repeat it with `zabgrab fetch -t NAME`:

```bash
zabgrab fetch -s demo -i "host=demo1,name=Particles *" --from "24 hours ago" --to now \
    --dry-run --save-template particles
```

The template gets the settings from the command line, from any templates used and
from your answers to questions, exactly as given (patterns and relative times, not
item IDs and dates). `[defaults]` and built-in settings aren't copied, so changing
them later still applies. It's saved to your user config, or `--save-to project`,
`system` or a file path; the rest of the file is left as it is. An existing template
is only replaced with `--force`.

Times can be ISO 8601 (`2026-10-14 09:00`, `2026-10-14T09:00:00Z`), natural language
(`now`, `24 hours ago`, `yesterday`) or day-first dates (`14/10/2026`), in the
`timezone` setting (default: local time).

`-o`/`--output` takes a file name, a directory (ending in `/`), or no value for an
automatic name built from `output_pattern` (default
`{hosts}_{templates}_{from}--{to}`). The format comes from `--format`, the file
extension, or the `format` setting. Saved files start with `#` comment lines
containing metadata (as JSON) describing exactly what was fetched; numpy and pandas
skip them with `comment="#"`.

## Output formats

| Extension | Format | Notes |
|---|---|---|
| `.mat` | MATLAB v7.3 | Load with `load` or `zabgrab_load` (below) |
| `.h5` | HDF5 | Same layout; read with h5py, or `h5read`/`zabgrab_load` in MATLAB |
| `.tsv`, `.csv` | Tab/comma-separated | Metadata in leading `#` lines |

`.mat` and `.h5` files hold one group per fetch (`fetch_0001`, `fetch_0002`, ...),
each with its metadata and one struct per item:

```matlab
S = load('particles.mat');
S.fetch_0001.items.item_12345.value      % N x 1 values
S.fetch_0001.items.item_12345.time       % POSIX seconds (UTC)
```

Text items are cell arrays in `.mat` files. Each text value takes about 480 bytes
there (several times more than in `.h5`), so zabgrab warns before writing more than
`mat_text_warn_entries` (default 200,000); `--allow-large-text` skips the warning.

## Keeping a file up to date

```bash
# Initial fetch of the last week into a MATLAB file
zabgrab fetch -t particles --from "7 days ago" --to now --output particles.mat

# Later (e.g. daily from cron): append everything since the last fetch
zabgrab update particles.mat
```

`update` repeats the last fetch from where it ended, with the same items and settings,
and adds the new data as another group. `--rematch` looks items up again from the
original templates (picking up new items); `--to`, `--server` and `--dry-run` are also
available. `zabgrab fetch ... --append` adds a fetch of your choice to a file.

The groups are only a storage detail. To load all of a file's data as one
continuous dataset in MATLAB, use `matlab/zabgrab_load.m` from this repository:

```matlab
addpath('/path/to/zabgrab/matlab')
S = zabgrab_load('particles.mat');       % one struct per item
plot(S(1).time, S(1).value)              % time is a UTC datetime
title(sprintf('%s: %s [%s]', S(1).host, S(1).name, S(1).units))
```

Or flatten a file into a new one with all groups merged (repeated points are kept
once), optionally converting it and filtering items or times:

```bash
zabgrab export particles.mat -o particles.csv --item "*0.3um" --from "2 days ago"
```

## Plotting

With the `plot` extra installed (`pip install 'zabgrab[plot]'`):

```bash
zabgrab plot particles.h5                          # show a file's data in a window
zabgrab plot particles.h5 -o particles.png         # or save it (.png, .pdf, .svg, ...)
zabgrab plot particles.h5 --item "*0.3um" --from "2 days ago"
zabgrab plot particles.h5 --log-y                  # log scales for the vertical axes
zabgrab fetch -t particles --plot                  # plot a fetch without saving it
```

All of a file's groups are plotted as one dataset; `--item`, `--from`, `--to` and
`--group` select what to plot, as for `export`. Items with the same units share an
axis. Hourly trends are drawn as the average with a min/max band. Lines only break
over times that no fetch covered, so sparse data and appended groups join up. Text
items can't be plotted: they're left out with a warning, or it's an error if nothing
else is selected. With `--log-y`, values of zero or less can't be shown; zabgrab says
which lines have them.

## Credits
Sean Leavey <sean.leavey@stfc.ac.uk>

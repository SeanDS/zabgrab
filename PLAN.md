# Zabgrab rewrite plan

Zabgrab is being rewritten from scratch. No backwards compatibility with the existing
code, config format or CLI is needed. This document records the agreed design, the
phased delivery plan, and the remaining open questions.

## Goals

- Find Zabbix items without digging IDs out of the web UI; wildcard item selection.
- Multi-server configs; templates that can run against one, several or a user-chosen
  server.
- Templates that can set every fetch argument; templates are stackable.
- Fast parallel downloads with clear progress, colour and helpful errors.
- MATLAB-friendly HDF5 output that can be incrementally updated.
- Optional plotting.
- Well tested, typed and documented.

## Platform and dependencies

- **Python >= 3.11** (stdlib `tomllib`). CI tests 3.11 to 3.14.
- **Zabbix server >= 7.0**. No support for older APIs.
- `src/` layout, setuptools + setuptools_scm, version read via `importlib.metadata`.

Dependency policy: standard library first, otherwise popular and actively maintained
libraries only. Prefer a small amount of our own code over a niche library.

| Purpose | Library |
|---|---|
| CLI | click |
| Colour, tables, progress bars, tracebacks | rich |
| Zabbix API (official library, async) | zabbix_utils[async] (pulls in aiohttp) |
| Config validation and errors | pydantic v2 |
| TOML reading | tomllib (stdlib) |
| TOML editing that keeps formatting | tomlkit |
| Natural-language dates | dateparser |
| Token storage | keyring |
| Arrays, HDF5 / MAT v7.3 | numpy, h5py |
| Plotting (`zabgrab[plot]` extra only) | matplotlib |
| Dev | pytest, pytest-asyncio, coverage, ruff (lint + format), mypy, pre-commit |

Dropped: pyzabbix, tomli, scipy, platformdirs (config paths are simple enough to do
ourselves; see below), tqdm (rich covers progress).

## Architecture

The code is split into layers, and dependencies only point inwards. The model and
service layers never import click, rich, matplotlib, zabbix_utils or h5py.

```
src/zabgrab/
  __init__.py
  __main__.py          # python -m zabgrab → cli.main()
  errors.py            # ZabgrabError (message, details, hint), ConfigError, InputRequired,
                       #   Cancelled, exit codes

  model/               # pure data, no I/O
    item.py            # Item (server, itemid, host, name, key, units, value_type), ItemSpec
    series.py          # Series (numeric raw / trend / text / log columns), FetchRecord, Coverage
    query.py           # Query (fully resolved fetch request) + provenance records
    choice.py          # Choice {choices, select}, choice_or()
    names.py           # template/server name and URL validation
    config.py          # pydantic config models, BUILTIN_DEFAULTS

  services/            # behaviour; depends on model + the ports below
    ports.py           # Protocols: ZabbixClient, UI (log/warn/confirm/prompt/choose/progress),
                       #   Clock, Editor, SecretStore, Writer/Reader (each added in the
                       #   phase that first needs it)
    config.py          # config loading with interactive repair of invalid files
    resolve.py         # CLI + templates + defaults → Query, with provenance, choice
                       #   resolution, stacked-template rules
    discover.py        # item spec / wildcard → Items
    fetch.py           # chunking, concurrency, retries → FetchRecord (drives UI progress)
    update.py          # FetchRecord metadata → follow-up Query
    dataset.py         # merged view over every FetchRecord in a file (see Plotting)
    naming.py          # automatic output filenames
    templates.py       # Query/CLI params → template table (for --save-template)

  adapters/            # implement the ports against real things
    zabbix.py          # ZabbixClient via zabbix_utils AsyncZabbixAPI
    configfiles.py     # path discovery, layered tomllib loading, tomlkit edits
    system.py          # SystemClock, ClickEditor
    secrets.py         # SecretStore via env vars + keyring
    formats/
      hdf5.py          # .h5 / .mat read, write, append
      delimited.py     # .tsv / .csv write

  ui/                  # presentation
    console.py         # rich implementation of the UI port (colours, tables, progress)
    selection.py       # parsing of "1,3,5-8" / "all" answers
    render.py          # rich renderers for items, hosts, explain, summaries
    plot.py            # matplotlib (optional; imported lazily)

  cli/                 # click commands: parse args → call services → render
    __init__.py        # group + global options (-v/-q/--debug/--color/--config/--isolated/--no-input)
    app.py             # AppContext (shared state, lazy config) + central error handling
    fetch.py update.py export.py items.py hosts.py templates.py config.py login.py plot.py
```

Rules:
- **Model, view, CLI:**
  - model: data classes only
  - services: the logic
  - ui: rendering only
  - cli: wiring only

  A CLI command looks like: parse → build adapters → call service → render result.
- **Dependency injection through ports.** Services receive a `ZabbixClient`, a `UI`, a
  `Clock`, a `SecretStore` and the readers/writers as arguments. This means:
  - tests use an in-memory fake Zabbix, a recording UI and a frozen clock;
  - services never prompt or print directly. They call `ui.prompt(...)`, and the fake
    UI scripts the answers.
- **Data goes to stdout; logs, warnings and progress go to stderr.**
- **Times:** datetimes are timezone-aware everywhere. They are stored as UTC POSIX
  seconds and displayed in local time unless `--tz` says otherwise.
- **Secrets:** never write them to config, output metadata or logs. Redact them in
  `--debug` output.

## Configuration

### Locations and precedence (lowest → highest)

1. Built-in defaults.
2. System: `/etc/zabgrab/config.toml` (Windows: `%PROGRAMDATA%\zabgrab\config.toml`).
3. User: `$XDG_CONFIG_HOME/zabgrab/config.toml`, defaulting to
   `~/.config/zabgrab/config.toml` on Linux **and macOS** (Windows:
   `%APPDATA%\zabgrab\config.toml`). `XDG_CONFIG_HOME` is respected on every OS.
4. Project: the nearest `zabgrab.toml`, searching upwards from the working directory
   and stopping at `$HOME` or the filesystem root.
5. `--config PATH` / `$ZABGRAB_CONFIG`. `--isolated` skips layers 2 to 4.
6. CLI flags.

There are no drop-in directories.

Merge rules: `[defaults]` is deep-merged. A named entry (`servers.X`, `templates.X`)
in a higher layer replaces the same entry from a lower layer whole.

`zabgrab config paths` lists the candidate files and whether each exists.
`zabgrab config show [--sources]` prints the merged config, optionally with the
originating file of each key. `zabgrab config edit [--target user|project|system]`
opens a file in `$EDITOR`.

### Schema

```toml
[defaults]
timezone = "local"                 # or e.g. "UTC", "Europe/London"
format = "mat"                     # mat | h5 | tsv | csv
output_dir = "."
output_pattern = "{hosts}_{templates}_{from}--{to}"
jobs = 6                           # parallel downloads
max_items = 50                     # confirm before wildcard expansions larger than this

[servers.demo]
url = "https://zabbix.demo.example.org"
auth = "token"                     # token | password
token_env = "ZABGRAB_DEMO_TOKEN"   # optional; otherwise the keyring (see zabgrab login)
verify_ssl = true
timeout = 30

[servers.lab2]
url = "https://zabbix.lab2.example.org"

[templates.particles]
description = "Particle counters, last 24h"
server = { choices = ["demo", "lab2"], select = "prompt" }
items = [
  { host = "demo1", name = "Particles *" },
  { key = "particle.count[*]" },
  { itemid = 12345 },
]
from = "24 hours ago"
to = "now"
resolution = "raw"                 # raw | 1h
agg = ["avg"]                      # with 1h: any of min, avg, max, count
format = "mat"
```

- **Templates can set every fetch argument.** CLI option names map one-to-one to
  template keys, for example `--from` → `from` and `--output-dir` → `output_dir`.
- **No template inheritance.**
- **Name rules:** template and server names must match `^[A-Za-z][A-Za-z0-9_-]*$`.
  This means names starting with `_` are reserved, which covers `__custom`. Invalid
  names are a config error that reports the file and key. The same check applies to
  `--save-template`.
- **Server values:** a configured server name or a literal URL (must include the
  scheme). An unknown name is an error with a "did you mean" suggestion.
- **Validation errors** point at the exact key, for example
  `templates.particles.items[1]: unknown field 'nmae'`.

### Choice values (`select`)

Keys that support it accept either a plain value or
`{ choices = [...], select = "all" | "prompt" | "first" }`:

| `select` | Behaviour |
|---|---|
| `all` | Run against every choice and stack the results into one output. |
| `prompt` | Interactive: the user picks one or more. Non-interactive: an error unless the CLI sets the value. |
| `first` | Use the first choice that works (failover). |

Supported on: `server`, `host` (inside item specs), `format`.
Repeating a CLI flag (`--server a --server b`) means `all`.

A missing `server` (nowhere in the CLI, the template or `[defaults]`) is prompted for.
See "Missing and invalid input".

### Saving a CLI call as a template

`zabgrab fetch ... --save-template NAME [--save-to user|project|system|PATH] [--force]`

- Saves only parameters whose click `ParameterSource` is the command line or an
  environment variable, so built-in defaults are not frozen into the template.
- Saves values as the user typed them: wildcard patterns rather than resolved item
  IDs, and relative times such as `"24 hours ago"`.
- Edits only the target file, using tomlkit so comments and whitespace survive. Writes
  to a temporary file and then replaces the original.
- Refuses to overwrite an existing template without `--force`. Prints the TOML that was
  written.

## Missing and invalid input

Terminology: a **server** is a Zabbix server (URL). A **host** is a monitored machine
inside Zabbix (used in item specs).

### Policy

When a required input is missing or invalid:
- **Interactive and convenient to answer:** prompt for it.
- **Not interactive, or not convenient to prompt:** exit with an error saying exactly
  how to fix the call. The message gives the flag to add, or the config snippet and the
  file to put it in (`zabgrab config paths` / `config edit`).

**Interactive** means stdin and stderr are both TTYs and none of these are set:
`--no-input`, `ZABGRAB_NO_INPUT=1`, or `CI`. Prompts are written to stderr, so piping
stdout keeps working.

**Implementation:** services ask through the `UI` port, for example
`ui.choose(question, options, fix=...)`. Every call must include a `fix` remedy
message. The non-interactive UI raises `InputRequired(fix)`, which the CLI turns into a
red error, the remedy, and exit code 1. This keeps the policy uniform and testable.

Prompts use rich: numbered choice lists, text input with validation, and confirmation.
That avoids an extra dependency for arrow-key menus.

**Showing the equivalent command:** if anything was answered interactively, the run
ends with the equivalent command line, e.g.
`Next time: zabgrab fetch --server demo --item 'host=demo1,name=Particles *'`. It also
suggests adding `--save-template NAME`.

### Cases

| Situation | Interactive | Non-interactive remedy |
|---|---|---|
| No server | Choose from configured servers, each shown with its URL, plus **"Custom…"**. Custom asks for a URL (`host[:port]`; `https://` added if no scheme is given). The URL is checked with `apiinfo.version`. Then offer to save it under `[servers.NAME]` in the user config. | `--server NAME\|URL`, or add `server = "..."` to the template, or `[defaults] server`; shows a `[servers.x]` snippet |
| Template `server` with `select = "prompt"` | Choose from the template's choices, plus "Custom…" | `--server` |
| No items (no template, no `--item`) | Item picker: enter a search pattern (and optionally a host, chosen from a list) → table of matches → pick numbers/ranges (`1,3,5-8`) or "all" | `--item SPEC` examples, or `-t NAME`; lists available templates |
| Wildcard matches more than `max_items` | Choose: fetch all / narrow to a host (from the matched hosts) / pick from table / cancel | `--yes`, or a narrower spec (e.g. `host=`) |
| Unknown template or server name | If close matches exist: "Did you mean X?". Otherwise choose from all names | Lists the close matches and `zabgrab templates list` / `config show` |
| Unparseable `--from`/`--to` | Re-ask with the parse error and examples | Error with examples of supported formats |
| Stacked templates disagree on from/to/resolution/agg | Show each template's value, then ask for the value to use | Error naming the flag to set |
| No token for the server | Choose: paste a token now (opens the token page; offer to save to keyring) / use username+password / cancel | `zabgrab login SERVER`, or set `token_env`'s variable |
| Username/password (`auth = "password"`) | Prompt (username defaults to the config value) | Use a token: `zabgrab login SERVER` |
| Output file exists | Choose: append (`.h5`/`.mat` only) / overwrite / new name (suggested `-2` suffix) / cancel | `--append`, `--overwrite`, or another `--output` |
| Fetched but no `--output`/`--plot` | "Save as `<auto name>`? [Y/n/other path]" | Warn only (data discarded); suggest `--output` |
| Config file invalid (TOML syntax or schema) | Show the error with file, line and key, then offer to open the file in `$EDITOR` and reload | Error with file, line, key and the expected type or values |
| Large text volume in `.mat` | Warn with size/time estimate; continue / write `.h5` instead / skip text items / cancel | Continue with a warning (see "Text in HDF5") |
| Plot extra not installed | No prompt (never install automatically) | ``pip install 'zabgrab[plot]'`` |
| `update` on CSV/TSV or a file with no zabgrab metadata | No prompt | Explain why; suggest `fetch --output x.h5` |

`--yes` accepts the **default answer** of confirmation prompts. It never chooses
overwrite or another destructive option. Those always need an explicit flag.

## Items

Item spec syntax:
- CLI: `--item 12345`, or `--item 'host=demo1,name=Particles *'`, or
  `--item 'key=particle.count[*]'`, or `--item 'tag=area:lab'`.
- Config: the equivalent tables.
- `*` wildcards map to Zabbix item search with wildcards enabled.

Resolution rules:
- The history type is taken from the item's `value_type`. The user never supplies it.
- All value types are supported:

  | Value type | Kind | Stored as |
  |---|---|---|
  | 0 | numeric float | `double` |
  | 3 | numeric unsigned | `uint64` (no precision loss above 2^53) |
  | 1 | character | text |
  | 4 | text | text |
  | 2 | log | text, plus the extra log columns (see Output) |

- Text items have no trends. With `resolution = "1h"`, any text items that match are
  skipped with a warning, and `--dry-run` lists them.
- If a wildcard expands to more than `max_items` items, prompt (see "Missing and
  invalid input"). `--yes` accepts the full list.
- Items matched by more than one spec or template are fetched once, and a log message
  says so.

Discovery commands:
- `zabgrab hosts [PATTERN] [--group G]`
- `zabgrab items [PATTERN] [--host H] [--key K] [--tag T]`

Both print rich tables. For items the columns are itemid, host, name, key, type,
units, last value and age. Zabbix permissions mean users only see what they're
allowed to.

## Fetch

`zabgrab fetch [-t TEMPLATE]... [--item SPEC]... [--server S]... [--from] [--to]
[--resolution raw|1h] [--agg ...] [--output [PATH]] [--format] [--append|--overwrite]
[--group NAME] [--dry-run] [--explain] [--plot] [--save-template NAME] [--yes]
[--jobs N]`

### Resolution and provenance

`services/resolve.py` merges built-in defaults, `[defaults]`, the templates and the CLI. It records
where every value came from: the CLI, a template name, a config file, or a built-in
default.

- When a CLI flag overrides a template value, log it at info level. Example:
  `--from "1 hour ago" overrides "24 hours ago" from template particles`.
- `--explain` prints the resolved query with the source of each value, then exits.
- `--dry-run` resolves servers, items and time range, prints the fetch plan, and exits.

### Stacked templates (`-t a -t b ...`)

- Templates may differ in server(s) and items. The results are stacked into one output.
- **`from`/`to`:** compare the values that the templates set themselves, after
  resolving them against the same base "now". This means `"24 hours ago"` and
  `"1 day ago"` count as equal.
  - If they all agree, or only one template sets them, use that value.
  - If they disagree, print a warning listing each template's value. Then prompt for
    new `from`/`to` when interactive, or exit with an error when not.
  - If `--from` or `--to` is on the CLI, use it and log that the templates' own values
    are being ignored.
- **`resolution`/`agg`:** the same rule as `from`/`to`. Templates that set different
  values get a warning, then a prompt when interactive or an error when not. A CLI
  `--resolution`/`--agg` wins, and a log message says the templates' values are ignored.
- Item specs and `--item` flags on the CLI are added to the templates' items.

### Downloading

- Uses zabbix_utils `AsyncZabbixAPI`, with concurrency limited by `--jobs` and retries
  with exponential backoff on transient errors.
- Each item's time range is split into chunks, by default one day for raw data and
  longer for trends. This bounds response sizes and lets progress bars move.
- Sorted by `clock`.
- **`resolution = "raw"`:** `history.get`.
- **`resolution = "1h"`:** `trend.get`. Hourly min/avg/max/count is the only trend
  data Zabbix keeps, so zabgrab does no resampling of its own.
  - If raw data is requested outside an item's history retention, warn and suggest
    `--resolution 1h`.
  - The README documents the Zabbix-side way to get other averages: a calculated item
    such as `avg(//key,5m)`.
- **Progress:** rich, written to stderr. One bar per active item plus an overall bar
  (chunks done / total). Hidden when stderr is not a terminal.
- **Summary at the end,** in green, for example
  `Fetched 3 items (12,345 points) from demo 2026-10-13 10:15 → 2026-10-14 10:15 UTC → demo1_particles_20261013T101500Z--20261014T101500Z.mat`.

### Error handling and UX

- Exit codes:

  | Code | Meaning |
  |---|---|
  | 0 | Success |
  | 1 | Error |
  | 2 | Usage error (click) |
  | 3 | Partial failure (some items failed; the others were saved) |

- Errors are caught centrally, giving one red line plus a hint. `--debug` shows the
  full rich traceback. Hints include:
  - SSL failure → mention `verify_ssl = false`
  - connection refused or timeout → show the URL
  - authentication failure → `zabgrab login SERVER`
  - empty result → check the time range, retention, or `--resolution 1h`
  - unknown template or server → "did you mean"
- `verify_ssl = false` prints one yellow warning and suppresses urllib3's per-request
  warnings.
- Warn if data was fetched but there is no `--output` or `--plot`. Suggest `--output`.
- Colours:

  | Style | Used for |
  |---|---|
  | green | success |
  | red | errors |
  | yellow | warnings |
  | bold | emphasis |
  | cyan | IDs, paths, URLs |
  | dim | debug output |

  `--color/--no-color`, `NO_COLOR` and non-terminal output all turn colour off.

## Output

### `--output` and automatic naming

| Invocation | Behaviour |
|---|---|
| `--output` absent | Nothing is saved; zabgrab warns unless `--plot` is given. |
| `--output` with no value | Automatic filename in `output_dir`. |
| `--output DIR/` | Automatic filename in `DIR`. |
| `--output PATH` | Exactly that path. The extension sets the format unless `--format` is given. |

Default pattern: `{hosts}_{templates}_{from}--{to}`.
Example: `demo1+demo2_particles+temps_20261013T101500Z--20261014T101500Z.mat`

- `{hosts}`: the matched hosts, sorted and joined with `+`. More than 3 become
  `a+b+5more`.
- `{templates}`: template names in CLI order, joined with `+`.
  - No templates: `__custom`.
  - If CLI flags changed data-affecting template values (items, server outside the
    template's choices, from/to, resolution, agg) or added items, append `+__custom`.
  - Changes to output, format or verbosity don't count.
- `{from}`/`{to}`: the resolved range in UTC, written like `20261014T101500Z` (Windows
  safe, sorts correctly).
- Other placeholders available to custom patterns: `{server}`, `{resolution}`.
- Characters outside `[A-Za-z0-9._+-]` become `-`. The name is limited to about 120
  characters by shortening the host list first.
- If the file already exists, add a suffix `-2`, `-3`, and so on. Never overwrite
  silently.

### HDF5 (`.h5`) and MAT v7.3 (`.mat`)

Both formats share one layout, written with h5py:

```
/fetch_0001                         one group per fetch (= one MATLAB variable)
    meta            string  JSON metadata (see below)
    t_from, t_to    double  POSIX seconds, UTC
    items/
        item_12345/
            itemid, host, name, key, units, server   string
            value_type  int
            time    double  N×1, POSIX seconds UTC
            value   N×1: double (type 0), uint64 (type 3), text (types 1, 2, 4)
            log_time, source, severity, logeventid  (log items only)
            min, avg, max, count                    (1h trends; the ones selected by agg)
        item_67890/ ...
/fetch_0002 ...
```

- **`.h5` is plain HDF5:** no MATLAB header and no MATLAB attributes. Strings are
  UTF-8 variable-length strings. MATLAB reads it with `h5read`/`h5info`, Python with
  h5py.
- **`.mat` adds only what MATLAB's `load` needs:**
  - the 512-byte user block with the v7.3 header,
  - `MATLAB_class` attributes (`struct`, `double`, `char`),
  - strings as `uint16` char arrays,
  - vectors stored as HDF5 shape (1, N) so that MATLAB sees N×1,
  - `MATLAB_empty` for empty arrays.

  After `S = load('file.mat')`, `S.fetch_0001.items.item_12345.value` works.
- Datasets are chunked with gzip compression (MATLAB supports deflate).
- **Text values** (see "Text in HDF5" below):
  - `.h5`: one variable-length UTF-8 string dataset per column. MATLAB's `h5read`
    returns a string/cell array.
  - `.mat`: MATLAB cell arrays of char, stored the way MATLAB itself stores them (see
    "Text in HDF5").
- Group names follow MATLAB identifier rules.
- **Appending:**
  - If the target exists and neither `--append` (adds the next `fetch_NNNN`) nor
    `--overwrite` is given, prompt (see "Missing and invalid input").
  - `--group NAME` sets a custom group name.
  - Appending works on a temporary copy that then replaces the original, so a crash
    can't corrupt the file. `--in-place` skips the copy for very large files.
  - `--append` with an empty `--output` is an error that suggests `zabgrab update`.

### Text in HDF5

Plain `.h5` is easy, because HDF5 has native variable-length UTF-8 strings. `.mat` is
harder, because MATLAB's `load` does not understand HDF5 strings:

- MATLAB `char` arrays are 2-D `uint16` (UTF-16 code units). A column of N strings
  would have to be an N×maxlen matrix padded with spaces. That wastes space, and
  trailing spaces in the data would be lost.
- The native representation is a **cell array**:
  - a dataset of HDF5 object references (`MATLAB_class = "cell"`),
  - each reference points to its own small `uint16` char dataset,
  - those datasets live in a hidden `/#refs#` group.

  This gives the natural `value{3}` in MATLAB, but it means one HDF5 dataset per
  string. A large log item (100k+ entries) could be slow to write and to `load`, and
  the file would be big.
- MATLAB's `string` class is an opaque object type and cannot be written at all.
- Characters outside the Basic Multilingual Plane must be encoded as UTF-16 surrogate
  pairs. Encoding with `utf-16-le` handles this.
- Appending and `update` need `/#refs#` to stay consistent across groups. The
  crash-safe copy-and-replace covers this.

Decision: **always use cell arrays in `.mat`**, and guard large writes.

- **Threshold:** a total number of text entries in the file after the write (existing
  plus new). Set by `[defaults] mat_text_warn_entries`. The default value will be
  calibrated by the phase 4 benchmark; something like 50,000 is the expected order of
  magnitude.
- **Interactive, threshold exceeded:**
  1. Warn with the entry count, the estimated file size and a rough MATLAB `load`
     time (from the benchmark).
  2. Then offer:
     - continue (the default),
     - write `.h5` instead (efficient text; read with `h5read` or `zabgrab_load`),
     - skip the text items,
     - cancel.
- **`--allow-large-text`** continues without asking. `--yes` also takes the default
  (continue).
- **Non-interactive:** continue, with a yellow warning that names
  `--allow-large-text` and the `.h5` alternative.
  - Reason: unattended runs (e.g. `zabgrab update` from cron) shouldn't start failing,
    and lose data, just because a file grew past a threshold. Slow loading is a
    nuisance that can be recovered from; missing data is not.
- **Accepting is remembered** in the file's metadata (`large_text_accepted`), so later
  `update` runs on the same file don't ask again.

### Delimited (`.tsv`, `.csv`)

- Metadata goes in `# ` comment lines at the top. numpy and pandas read the file with
  `comment='#'`.
- One header row, then a time/value column pair per item (or time/min/avg/max/count for
  trends). Times are ISO 8601 with offset.
- Shorter series are padded with empty cells across **all** of their columns. This
  avoids the old misalignment bug.
- Text is written with the stdlib `csv` module and full quoting rules. Values that
  contain tabs, commas, quotes or newlines are quoted. Multi-line log entries need a
  CSV-aware reader such as pandas, not `numpy.loadtxt`.
- These formats cannot be appended to, so `--append` and `update` give an error.

### Metadata (every format)

The JSON contains:
- the zabgrab version and output format version
- the run time (UTC) and the full command line
- the server name and URL (never credentials)
- the templates used, both as written and as resolved, and the item specs as written
- the matched items: itemid, host, name, key, units, value_type
- the resolved from/to, the timezone, the resolution and agg
- the provenance of each resolved value

Together these are enough to reproduce or extend the query.

## `zabgrab update FILE`

Extends an existing `.h5`/`.mat` file with new data:

1. Read `meta` from the last `fetch_NNNN` group.
2. Set `from` to the previous `t_to` + 1 s and `to` to `now`, or `--to`.
   - For 1h trends, the previous `t_to` was clamped to the last completed hour when
     it was fetched, so partial hours are never missed.
3. Choose the server:
   - Look up the stored server name in the current config to get credentials.
   - If that name is gone, fall back to the stored URL with a warning.
   - `--server` overrides.
4. Choose the items:
   - By default, reuse the previously resolved item IDs so that columns stay
     consistent.
   - `--rematch` re-expands the original wildcard patterns, which picks up new items.
5. Fetch and append as `fetch_NNNN+1`, using the same crash-safe copy-and-replace.
   - If no new data arrives, nothing is appended and zabgrab says so.

Options: `--to`, `--server`, `--rematch`, `--dry-run`, `--in-place`.

README example to include:

```bash
# Initial fetch of the last week into a MATLAB file
zabgrab fetch -t particles --from "7 days ago" --output particles.mat

# Later (e.g. daily from cron): append everything since the last fetch
zabgrab update particles.mat
```

plus a MATLAB example that uses `zabgrab_load` (see below) to get the merged
timeseries.

## Authentication

- Zabbix 7 API tokens are sent as a Bearer header by zabbix_utils.
- Token lookup order: `token_env` environment variable, then keyring entry for the
  server URL, then an error suggesting `zabgrab login SERVER`.
- `auth = "password"`: prompt for the username (defaulting to `username` from the
  server config, if set) and the password every time. Non-interactive runs error.
- `zabgrab login SERVER`:
  1. Opens `<url>/zabbix.php?action=user.token.list` in the browser.
  2. Prompts the user to paste the token (hidden input).
  3. Checks it with a test API call.
  4. Stores it in the keyring.
- `zabgrab logout SERVER` removes the keyring entry.

## Merged view, plotting and export

### Fetch groups are a storage detail

`fetch_NNNN` groups exist only because of how appending works. By default every
reading operation sees **one merged dataset** (`services/dataset.py`):

- **Series identity:** a series is identified by (server URL, itemid, resolution, agg).
  The same item from different groups is the same series.
- **Merging:** each series is concatenated across groups and sorted by time. Points
  with an identical timestamp are deduplicated, keeping the later group's value.
- **Coverage:** the union of each group's `[t_from, t_to]` intervals. Plots break
  lines **only over periods that no fetch covered**. Sparse data inside a covered
  period is still joined up, so appended groups never show visible seams.
- **Metadata:** item name and units come from the most recent group. If the units
  changed between groups, warn.
- `--group NAME` (repeatable) limits any reading command to specific groups when
  wanted.

### Plotting (`pip install 'zabgrab[plot]'`)

- `zabgrab plot FILE [--item PATTERN] [--from] [--to] [--group NAME]...` plots the
  merged dataset. `--from`/`--to` set the plotted window.
- `zabgrab fetch ... --plot` plots the fetched data without saving.
- One axis per unit, labelled from the metadata as `host: name [units]`.
- Trends are drawn as the avg line with a min/max band.
- **Text items:**
  - If the selection contains only text items, error: `item 12345 (demo1: Syslog) is
    a log item; text items can't be plotted`.
  - If the selection is mixed, plot the numeric items and warn about the skipped text
    items.
- If matplotlib is missing, error with the install command.

### Export

`zabgrab export FILE --output OUT[.csv|.tsv|.h5|.mat] [--item] [--from] [--to]` writes
the merged dataset as a single group. Use it to flatten an often-updated file, or to
convert it to CSV.

### MATLAB helper

`matlab/zabgrab_load.m` is shipped in the repo and documented in the README.
`S = zabgrab_load('particles.mat')` returns the merged series per item, with `datetime`
times. This makes groups invisible to MATLAB users too. It is tested manually
alongside the MATLAB check files.

## Testing

- **Mocked Zabbix server, used at two levels and fed from the same data
  (`tests/fixtures/zabbix_seed.py`):**
  - `FakeZabbixClient`: an in-memory implementation of the `ZabbixClient` port, for
    service tests. It is fast, needs no network, and can inject errors, timeouts and
    slow responses.
  - A fake Zabbix 7 JSON-RPC HTTP server built on aiohttp's test server (no extra
    library). It is used for tests of the zabbix_utils adapter and for CLI end-to-end
    tests. It implements `apiinfo.version`, `user.login`/`logout`, `host.get`,
    `item.get` (search, wildcards, filters), `history.get` and `trend.get`, including
    authentication failures.
- **Service tests** use the fake client, a recording UI (captures messages and
  scripts the prompt answers) and a frozen clock. They need no click, rich or files
  beyond `tmp_path`.
- **Prompt tests:** every prompt site is tested twice, once with scripted answers and
  once with the non-interactive UI. The second test asserts the exact remedy message.
- **Unit tests:** item spec parsing, choice resolution, query merging and provenance,
  stacked from/to rules, filename building, config discovery and merging (with
  `XDG_CONFIG_HOME`, `$HOME` and cwd faked using `tmp_path`/monkeypatch), name
  validation, date parsing with a fixed "now" and timezone.
- **Format tests:**
  - round-trip write/read/append for `.h5`/`.mat`
  - check the `.mat` header and `MATLAB_class` attributes
  - TSV padding with series of different lengths
  - all value types, including text with non-ASCII characters, tabs, newlines and
    characters outside the BMP
  - merged-view tests: overlap deduplication, coverage gaps, units changes
  - a performance benchmark for large text items in `.mat`, which sets the default
    `mat_text_warn_entries` and the estimates shown in the prompt
  - the large-text prompt: interactive choices, `--allow-large-text`, the
    non-interactive warning, and the remembered acceptance on `update`
- **CLI tests:** `CliRunner` end-to-end against the fake server. Cover exit codes,
  stdout/stderr separation, the `--explain` and `--dry-run` output, and the
  `--save-template` round trip (tomlkit leaves the file otherwise byte-identical).
- **Regression tests for bugs in the old code:**
  - TSV column misalignment
  - mixing naive UTC and local times
  - config file passed as a file object
  - `-q` swallowing the exit on error
  - a zero exit code when every item fails
  - a cached unauthenticated client after a failed login
- **Optional integration job:** a Zabbix 7 docker-compose stack in CI (manual or
  nightly).
- **MATLAB checks (manual, by Sean):**
  - `tests/matlab/generate.py` writes sample `.mat`/`.h5` files covering: raw data,
    trends, empty items, appended groups, non-ASCII names, char/text/log items, and
    a large log item.
  - `tests/matlab/check_files.m` loads them and asserts their structure and values.
    It also exercises `zabgrab_load.m`.
  - Once verified, the files are kept as reference outputs in the Python test suite.
- **CI (GitLab):**
  - ruff, mypy (strict on `src/`), pytest with coverage, on a Python 3.11 to 3.14
    matrix via `parallel: matrix`
  - build the package, then smoke-test the installed wheel

## Documentation

- **README:**
  - install, including `[plot]`
  - quick start, `zabgrab login`, finding items (`hosts`/`items`)
  - a **complete commented config example** (servers, defaults, templates with
    choices and wildcards)
  - stacking templates, output naming, formats and MATLAB/Python loading snippets
  - the `update` example, trends vs raw, and the calculated-item note for other
    averages
- A test parses the README's TOML example so it cannot go stale. Ship the same
  example as `config.example.toml`.
- **CHANGELOG:** the rewrite is released as **1.0.0** and is a breaking change.
- **Docstrings:** all public functions have docstrings and type hints.

## Phases

| # | Scope | Done when |
|---|---|---|
| 1 ✅ | Project skeleton: src layout, pyproject (3.11+), ruff/mypy/pytest/pre-commit, GitLab CI matrix. Ports, model, `ui/console.py`, config adapter (paths, layering, validation), `config paths/show/edit` | CI green; config tests pass |
| 2 | Zabbix adapter, secrets adapter (env/keyring, password prompt), `FakeZabbixClient` + fake JSON-RPC server, item spec parsing, `services/discover.py`, `hosts`/`items` commands | Can list hosts/items against fake and real servers |
| 3 | `services/resolve.py` (provenance, override logs, choices, stacked-template rules), `services/fetch.py` (async, chunked, raw + 1h trends, all value types), progress bars, summary, exit codes, TSV/CSV, auto naming, `--dry-run`/`--explain` | End-to-end fetch to TSV |
| 4 | HDF5 + MAT v7.3 readers/writers (text included, with the cell-array benchmark), `--append`, `services/update.py` + `update` command, `services/dataset.py` + `export`, MATLAB check files + `zabgrab_load.m` | Sean confirms the files and the helper in MATLAB |
| 5 | `--save-template` (tomlkit), `login`/`logout` | Round-trip tests pass |
| 6 | `plot` extra (merged view, coverage gaps, text-item errors) | Plots from live fetch and from multi-group files |
| 7 | Full README, `config.example.toml`, CHANGELOG, release **1.0.0** | Docs reviewed; release tagged |

Each phase includes its tests and docstrings. The old code is deleted in phase 1.

## Decisions log

- Stacked templates: `from`/`to`/`resolution`/`agg` must agree, otherwise warn and
  then prompt (interactive) or error (non-interactive). CLI values win, with a log
  message.
- Release version: 1.0.0.
- Text items (value types 1, 2, 4) are supported in v1. Plotting them is an error.
- Text in `.mat` is always a cell array. Large volumes trigger a prompt
  (`--allow-large-text` skips it). Non-interactive runs continue with a warning.
- Append groups are a storage detail. Reading, plotting and exporting use the merged
  view by default.
- The untracked sample data files in the repo are out of scope.

## Open questions

None at present.

# Zabgrab rewrite plan

Zabgrab is being rewritten from scratch. No backwards compatibility with the existing
code, config format or CLI is needed. This document records the agreed design, the
phased delivery plan, and the remaining open questions.

## Goals

- Find Zabbix items without digging IDs out of the web UI; wildcard item selection.
- Multi-server configs; templates that run against one server, or one the user
  chooses when asked.
- Templates that can set every fetch argument. One template and one server per fetch.
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
| HTTP for the Zabbix API (own thin JSON-RPC client) | aiohttp |
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
service layers never import click, rich, matplotlib, aiohttp or h5py.

```
src/zabgrab/
  __init__.py
  __main__.py          # python -m zabgrab → cli.main()
  errors.py            # ZabgrabError (message, details, hint), ConfigError, InputRequired,
                       #   Cancelled, exit codes

  model/               # pure data, no I/O
    item.py            # ItemSpec (how users select items)
    itemspec.py        # --item string <-> ItemSpec
    zabbix.py          # Host, Item, ValueType
    server.py          # Server (a resolved server: name + ServerConfig)
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
                       #   resolution
    servers.py         # which server(s) to use: --server, config, prompt, "Custom…"
    session.py         # connecting and logging in (env/keyring token, paste, password)
    discover.py        # item spec / wildcard → Items; running actions across servers
    fetch.py           # chunking, concurrency, retries → FetchRecord (drives UI progress)
    update.py          # FetchRecord metadata → follow-up Query
    dataset.py         # merged view over every FetchRecord in a file (see Plotting)
    naming.py          # automatic output filenames
    templates.py       # Query/CLI params → template table (for --save-template)

  adapters/            # implement the ports against real things
    zabbix/
      transport.py     # HTTP JSON-RPC transport (aiohttp), network error hints
      api.py           # ZabbixAPI: implements the ZabbixClient port
    configfiles.py     # path discovery, layered tomllib loading, tomlkit edits
    system.py          # SystemClock, ClickEditor
    secrets.py         # SecretStore via the keyring
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
output_pattern = "{hosts}_{template}_{from}--{to}"
jobs = 6                           # parallel downloads
max_items = 50                     # confirm before wildcard expansions larger than this

[servers.demo]
url = "https://zabbix.demo.example.org"
auth = "token"                     # token | password
token_env = "ZABGRAB_DEMO_TOKEN"   # optional; otherwise the keyring (see zabgrab token add)
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
| `all` | Use every choice: every host (for `host`), or write every format (for `format`). |
| `prompt` (default) | Interactive: the user picks. Non-interactive: an error unless the CLI sets the value. |
| `first` | Use the first choice that works. |

Supported on: `server`, `host` (inside item specs), `format`. A fetch uses one
server, so a `server` choice only allows `prompt` (the user picks one; `--server`
picks without asking); `all` or `first` there is a config error. `fetch` accepts one
`--server` and one `--template`.

A missing `server` (nowhere in the CLI, the template or `[defaults]`) is prompted for.
See "Missing and invalid input".

### Saving a CLI call as a template

`zabgrab fetch ... --save-template NAME [--save-to user|project|system|PATH] [--force]`

- Saves the settings given on the command line, by the template used, and as
  answers at prompts. `[defaults]` and built-in values are not frozen into the
  template. Templates can't build on each other, so the new one is self-contained:
  it copies the template's items and settings (the command line winning).
- Saves values as the user typed them: wildcard patterns rather than resolved item
  IDs, and relative times such as `"24 hours ago"`. Items picked at a prompt are
  saved as the search pattern if all its matches were picked, otherwise as IDs.
- Works with `--dry-run` and `--explain` too. The name and any existing template are
  checked before anything is fetched. `--save-to` defaults to the user config.
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
- **Not interactive:** keep going with the input that was given, warning about
  anything assumed. Yes/no questions take their default answer with a warning that
  says how to choose otherwise. Questions with nothing to fall back on (which server,
  which token, which items) exit with an error saying exactly how to fix the call: the
  flag to add, or the config snippet and the file to put it in.
- **Not convenient to prompt:** exit with such an error too.
- **Exception:** a server redirect (e.g. HTTP to HTTPS) never causes a request to be
  resent non-interactively; the run fails and asks the user to fix the URL.

**Interactive** means stdin and stderr are both TTYs and none of these are set:
`--no-input`, `ZABGRAB_NO_INPUT=1`, or `CI`. Prompts are written to stderr, so piping
stdout keeps working.

**Implementation:** services ask through the `UI` port, for example
`ui.choose(question, options, fix=...)`. Every call must include a `fix` remedy
message. Non-interactively, `ui.confirm` warns (including the `fix`) and returns its
default; the other questions raise `InputRequired(fix)`, which the CLI turns into a red
error, the remedy, and exit code 1. This keeps the policy uniform and testable.

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
| No `from`/`to` (no built-in default) | Ask for each, with examples (e.g. "24 hours ago", "2026-10-14 09:00", "now") | `--from`/`--to`, or set `from`/`to` in the template or `[defaults]` |
| No `format` (no built-in default) | Use the output file's extension if it has a known one; otherwise choose from h5 (the default) / mat / tsv / csv | `--format`, an `--output` path with a known extension, or `format` in the template or `[defaults]` |
| Unparseable `--from`/`--to` | Re-ask with the parse error and examples | Error with examples of supported formats |
| No token for the server | Choose: paste a token now (opens the token page; offer to save to keyring) / use username+password / cancel | `zabgrab token add SERVER`, or set `token_env`'s variable |
| Username/password (`auth = "password"`) | Prompt (username defaults to the config value) | Use a token: `zabgrab token add SERVER` |
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
- Patterns follow the Zabbix API's wildcard search (`searchWildcardsEnabled`, see
  `zbx_db_search`): the whole value must match, ignoring case, with `*` matching
  any text; SQL's `%` and `_` are literal. Host patterns match the technical or
  visible name. (Unlike the web interface's filters, which match anywhere.)

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

`zabgrab fetch [-t TEMPLATE] [--item SPEC]... [--server S] [--from] [--to]
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

### One template and one server

- A fetch uses at most one template (`-t`) and one server: `--server`, else the
  template's, else `[defaults]`', else asked. Items given with `--item` come from the
  same server.
- A CLI value that differs from the template's is logged (times compare after
  parsing, so `"24 hours ago"` and `"1 day ago"` are the same).
- `agg` only applies to hourly trends: with raw data, an `agg` from the CLI or a
  template gets a warning that it's ignored (one from `[defaults]` doesn't), and an
  ignored `--agg` doesn't make the query custom.
- Item specs and `--item` flags on the CLI are added to the templates' items.

### Downloading

- Uses zabgrab's async `ZabbixAPI` client, with concurrency limited by `--jobs` and retries
  with exponential backoff on transient errors.
- Each item's time range is split into chunks, by default one day for raw data and
  longer for trends. This bounds response sizes and lets progress bars move.
- Sorted by `clock`.
- **`resolution = "raw"`:** `history.get`.
- **`resolution = "1h"`:** `trend.get`. Hourly min/avg/max/count is the only trend
  data Zabbix keeps, so zabgrab does no resampling of its own.
  - If data is requested from before an item's storage period (history for raw,
    trends for `1h`), warn that it may have been deleted, suggesting
    `--resolution 1h` for raw data when trends are kept longer. Items sharing a
    period share a warning; periods set by macros aren't known, so aren't checked.
  - The documentation describes the Zabbix-side way to get other averages: a calculated item
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
  - authentication failure → `zabgrab token add SERVER`
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

`--output` can be repeated to save to several files (e.g. `-o data.h5 -o data.csv`),
each handled as above; a path given twice is written once, and a format asked for
one output without an extension is reused for the others. `--append` then applies to
every output, so each must be a `.h5` or `.mat` file. `export -o` and `plot -o` can
be repeated too (`plot` draws the figure once and saves it in each format).

Default pattern: `{hosts}_{template}_{from}--{to}`.
Example: `demo1+demo2_particles_20261013T101500Z--20261014T101500Z.mat`

- `{hosts}`: the matched hosts, sorted and joined with `+`. More than 3 become
  `a+b+5more`.
- `{template}`: the template's name.
  - No template: `__custom`.
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
            log_time, source, severity, event_id    (log items only)
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
- the template used (in a list and a table, from when fetches could use several),
  as written and as resolved, and the item specs as written
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

Documentation example:

```bash
# Initial fetch of the last week into a MATLAB file
zabgrab fetch -t particles --from "7 days ago" --output particles.mat

# Later (e.g. daily from cron): append everything since the last fetch
zabgrab update particles.mat
```

plus a MATLAB example that uses `zabgrab_load` (see below) to get the merged
timeseries.

## Authentication

- Tokens and session IDs are sent as a Bearer header. Tokens are checked with
  `user.checkAuthentication` before use.
- Token lookup order: `token_env` environment variable, then keyring entry for the
  server URL. If neither exists (or the token is rejected), interactive users choose
  between pasting a token (offered to be saved in the keyring) and a username and
  password. Non-interactive runs get an error explaining how to store a token with
  `zabgrab token add` or set up `token_env`.
- `auth = "password"`: prompt for the username (defaulting to `username` from the
  server config, if set) and the password every time. Non-interactive runs error.
- `zabgrab token list [--check]`: every configured server plus any other server with
  a stored token, showing whether a token is stored and the `token_env` status (never
  the tokens). `--check` tests each token against its server.
- Tokens live in numbered keyring entries `zabgrab`/`token1`, `token2`, ... each
  holding `{"url", "token"}` as JSON. Keyring backends can't be enumerated, so
  zabgrab reads entries in order until the first missing one; deleting moves the
  last entry into the gap so numbering stays contiguous. `token reset` deletes
  entries until 256 consecutive numbers are empty, to catch leftovers beyond a gap.
- `zabgrab token add [SERVER] [FILE] [--no-check]`: shows the token page URL, reads
  the token from FILE (`-` for stdin) or asks for it (hidden input), checks it against
  the server unless `--no-check`, and stores it. Without SERVER, asks which
  (including "Custom…").
- `zabgrab token delete [SERVER] [--yes]`: confirms first (non-interactive runs need
  `--yes`). Without SERVER, asks which of the stored tokens.
- `zabgrab token reset [--yes]`: deletes all of zabgrab's keyring entries.

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

- `zabgrab plot FILE [--item SPEC] [--match PATTERN] [--from] [--to] [--group NAME]...
  [-o IMAGE]` plots the merged dataset (the same selection options as `export`
  and `info`).
  `--from`/`--to` set the plotted window. `-o` saves an image instead of showing a
  window, which also works without a display; repeat it to save several formats.
- `zabgrab fetch ... --plot` plots the fetched data without saving (or asking to),
  unless `--output` is given too.
- `--log` (on `plot`, and on `fetch` with `--plot`) uses log scales for the
  values (for histograms, the counts). Lines with values of zero or less, which
  can't be shown, get a warning. It was `--log-y` in 1.0, which still works,
  hidden and with a warning, until 2.0.
- `--hline` and `--vline` (`plot` only, repeatable) draw reference lines, each
  marking a position on its axis in that axis's terms: on time plots `--hline` is
  a value and `--vline` a time; on histograms `--hline` is a count (to see which
  bins exceed it) and `--vline` a value among the bins.
  - Numbers are `[LABEL=]VALUE [UNITS]` (`RefLine.parse`): the label is
    everything before the last `=` (else the value as given), and UNITS, if
    given, limit the line to the subplots with exactly those units (a warning if
    none match; otherwise every subplot).
  - Times are `[LABEL=]TIME`, parsed as for `--from` in the CLI (they can contain
    spaces, so no units; every subplot), drawn at wall-clock times like the data.
  - Drawn dark grey, each label with its own dash pattern across the figure, in
    the legend. Lines at zero or less on a log scale are warned about and left
    out.
- One axis per unit, lines labelled `host: name [units]`, plus `(hourly)` for
  trends and the server when the plot has several.
- Trends are drawn as the avg line (else whichever statistic was fetched) with a
  min/max band; a lone point's range is drawn as an error bar. A trend point (timed
  at the start of its hour) belongs to the fetched range its hour overlaps.
- Times are shown in the configured time zone, as wall-clock times (right across
  daylight saving changes).
- The logic (`services/plotting.py`) is separate from the matplotlib drawing
  (`ui/plot.py`), which is imported only when plotting.
- **Text items:**
  - If the selection contains only text items, error: `item 12345 (demo1: Syslog) is
    a log item; text items can't be plotted`.
  - If the selection is mixed, plot the numeric items and warn about the skipped text
    items.
- If matplotlib is missing, error with the install command.

### Export

`zabgrab export FILE --output OUT[.csv|.tsv|.h5|.mat] [--item] [--match] [--from]
[--to] [--group]` writes the merged dataset as a single group. Use it to flatten an
often-updated file, or to convert it to CSV.

### Info

`zabgrab info FILE` lists the merged items (ID, host, name, key, units, raw or
hourly, points, time range); `--groups` lists the fetch groups instead (name,
created, range, items and points selected). It takes the same selection options, so
it shows what `plot` and `export` would use. `--overlaps` (implying `--groups`) adds
a *Repeated* column: how many of each group's selected points another group also
holds (same series and time).

`fetch --append` notes (an info message, not a warning) each existing group whose
range overlaps the new fetch's for any of the same items at the same resolution,
pointing to `info --overlaps`. Ranges that only touch don't count, so `update`
(which starts where the last fetch ended) never triggers it, and it doesn't check.

### Listing values (`show`)

`zabgrab show [FILE]` lists data points, mainly for text and log items, which can't
be plotted, and for finding extreme values.

- **Source:** FILE (`.h5`/`.mat`), merged and selected as for `plot` and `export`
  (`--item`, `--match`, `--from`, `--to`, `--group`); without FILE, fetched as by
  `fetch` (`--template`, `--item`, `--server`, `--from`, `--to`, `--resolution`,
  `--agg`, `--yes`) but not saved. Options of the other mode are usage errors.
- **Order:** `--sort time` (default) interleaves every item's points in one table,
  with an *Item* column when there are several. `--sort value` (or `min`, `avg`,
  `max`, `count` for trends; `value` means the average) gives one section per item,
  since values of different items, often in different units, can't be ranked
  together. `--desc` reverses. Text sorts alphabetically.
- **Slicing:** `--limit`/`--offset` apply to the whole list in time order (with a
  "Showed values a-b of n" message on stderr) and to each section by value. With a
  limit, `heapq` picks the top N without sorting everything.
- **Filters:** `--grep` is a Python regular expression searched anywhere in the
  value as text (like `grep -E`; `(?i)` ignores case). `--above`/`--below` are
  exclusive, compare the sorted column (the average for trends), and leave out
  text. Filters apply before slicing.
- **Layout** (`ui/listing.py`): plain aligned columns, not rich tables, so long
  lists are quick and work in pagers and pipes. The value is the last column, so
  `--wrap` can continue multi-line text under it; otherwise line breaks show as
  `⏎`. Log columns that are empty or zero in every row shown are left out.
- **Paging** (`RichUI.page`): in a terminal, through `$PAGER` (empty means none)
  or `less` with `LESS=FRSX` unless set; cut to the terminal's width without one;
  plain whole lines when piped. `--no-pager` turns it off.
- `fetch --show` lists the fetched data in time order, without saving it unless
  `--output` is given (like `--plot`); sorting and filtering are `show`'s only.

### Bins (`show --bin`, `plot --bin`)

- `--bin 10,100,1000` (or repeated `--bin`) gives edges, strictly increasing and
  finite (`services/binning.py`). N edges make N+1 bins: `< first`, `a to b`,
  `≥ last`. Bins are half-open `[a, b)` like numpy's (`bisect_right`), so the last
  bin includes its edge.
- Numeric items only (text is left out: a note in `show`, a warning in `plot`,
  an error if nothing numeric is left). Hourly trends count the average (else the
  first statistic fetched), as `plot` draws them.
- `show --bin`: per item, a heading with the number of values and a
  Bin/Count/Share table. `--grep`/`--above`/`--below` apply first; `--sort`,
  `--desc`, `--limit`, `--offset` and `--wrap` are usage errors with it.
- `plot --bin`: a `HistogramSpec` (`services/plotting.py`) drawn by
  `ui/plot.draw_histogram`: bins along the bottom (lowest on the left), counts
  up the side. Equal-width bins labelled with their ranges (open bins have no
  width, and real widths can differ by orders of magnitude), items' bars side by
  side, one subplot per unit, `--log` on the counts. A `--vline` value goes on
  the boundary for an edge, in proportion between a closed bin's edges, and
  across the middle of an open-ended bin (with a warning suggesting the value as
  an extra edge).
- Counts are numbers of points, proportional to time only for evenly sampled
  items; weighting by time could come later.

### Selecting from files

`plot`, `export` and `info` select items with:
- `--item SPEC`: an item spec, matched exactly as the Zabbix API would match it
  (whole values, ignoring case, `*` wildcards; host by technical or visible name;
  every given field must match; tags exactly, as the API's tag filters compare
  them), so `info FILE --item X` selects what `items --item X` found. `tag`
  matches the tags stored with each item; items in files written before tags were
  stored have none.
- `--match PATTERN`: an `fnmatch` pattern (`*`, `?`, `[seq]`, `[!seq]`) matched
  case-insensitively against the whole of each
  item's ID, name, key or `host: name`.

An item is used if any `--item` or `--match` selects it (or neither is given). When
`plot` or `export` selects nothing, the hint suggests `zabgrab info FILE`.

### MATLAB helper

`matlab/zabgrab_load.m` is shipped in the repo and documented in the user guide.
`S = zabgrab_load('particles.mat')` returns the merged series per item, with `datetime`
times. This makes groups invisible to MATLAB users too. It is tested manually
alongside the MATLAB check files.

## Testing

- **Mocked Zabbix server** (`tests/fakezabbix.py`): `FakeZabbix` implements the
  API methods zabgrab uses over in-memory data (`apiinfo.version`, `user.login`/
  `logout`/`checkAuthentication`, `hostgroup.get`, `host.get`, `item.get`, and later
  `history.get`/`trend.get`), with Zabbix's search semantics, auth failures and
  injectable errors. It's used at two levels, with the same seed data:
  - through `FakeTransport`, in memory, so service and CLI tests exercise the real
    `ZabbixAPI` client without a network;
  - served over HTTP with aiohttp's test server, to test the real `HttpTransport`.
- **Service tests** use the fake client, a recording UI (captures messages and
  scripts the prompt answers) and a frozen clock. They need no click, rich or files
  beyond `tmp_path`.
- **Prompt tests:** every prompt site is tested twice, once with scripted answers and
  once with the non-interactive UI. The second test asserts the exact remedy message.
- **Unit tests:** item spec parsing, choice resolution, query merging and provenance,
  override rules, filename building, config discovery and merging (with
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
- **MATLAB checks (wherever MATLAB is installed):**
  - `tests/matlab/generate.py` writes sample `.mat`/`.h5` files covering: raw data,
    trends, empty items, appended groups, non-ASCII names, char/text/log items, and
    a large log item.
  - `tests/matlab/check_files.m` loads them and asserts their structure and values.
    It also exercises `zabgrab_load.m`.
  - `tests/test_matlab.py` runs `check_files.m` in every MATLAB it finds (`matlab` on
    `PATH`, `/Applications/MATLAB_*.app`, or the executables in `ZABGRAB_MATLAB`),
    and is skipped when there is none, as in CI.
  - Once verified, the files are kept as reference outputs in the Python test suite.
- **CI (GitHub Actions, `.github/workflows/ci.yml`):**
  - pre-commit hooks (ruff check and format, etc.) and mypy
  - pytest with 100% coverage on Python 3.11 to 3.14 (Linux), plus the newest Python
    on macOS, where the tests run as a normal user rather than root, and on Windows
  - build the package, then smoke-test the installed wheel

## Documentation

- **User documentation** is a Sphinx site (Alabaster theme) in `docs/`, focused on
  using zabgrab rather than on configuration: installation, a quickstart, guides
  to finding items, fetching, templates, updating files, analysing the data
  (plotting, MATLAB and Python loading, file layouts) and running unattended, a
  light configuration page, and a command reference generated from the click
  commands (sphinx-click). GitHub Pages serves the latest release's documentation at
  the root and `main`'s at `/dev/` (with a banner saying it's unreleased).
- **Developer documentation** in the same site: contributing (setup, checks, code
  layout, conventions), making a release (tagging, GitHub release, PyPI), and an
  API reference generated from the docstrings.
- **README:** installation, running zabgrab, and where to find or how to build the
  documentation; nothing about configuration or advanced use.
- `config.example.toml` is a complete commented config example, included in the
  documentation; a test checks it's valid.
- **CHANGELOG** (`CHANGELOG.rst`, included in the documentation): the rewrite is released as **1.0.0** and is a breaking change.
- **Docstrings:** all public functions have docstrings and type hints.

## Phases

| # | Scope | Done when |
|---|---|---|
| 1 ✅ | Project skeleton: src layout, pyproject (3.11+), ruff/mypy/pytest/pre-commit, CI matrix. Ports, model, `ui/console.py`, config adapter (paths, layering, validation), `config paths/show/edit` | CI green; config tests pass |
| 2 ✅ | Zabbix adapter, secrets adapter (env/keyring, password prompt), `FakeZabbixClient` + fake JSON-RPC server, item spec parsing, `services/discover.py`, `hosts`/`items` commands | Can list hosts/items against fake and real servers |
| 3 ✅ | `services/resolve.py` (provenance, override logs, choices, stacked-template rules), `services/fetch.py` (async, chunked, raw + 1h trends, all value types), progress bars, summary, exit codes, TSV/CSV, auto naming, `--dry-run`/`--explain` | End-to-end fetch to TSV |
| 4 ✅ | HDF5 + MAT v7.3 readers/writers (text included, with the cell-array benchmark), `--append`, `services/update.py` + `update` command, `services/dataset.py` + `export`, MATLAB check files + `zabgrab_load.m` | Sean confirms the files and the helper in MATLAB |
| 5 ✅ | `--save-template` (tomlkit), "Next time" hint | Round-trip tests pass |
| 6 ✅ | `plot` extra (merged view, coverage gaps, text-item errors) | Plots from live fetch and from multi-group files |
| 7 ✅ | Sphinx documentation (published to GitHub Pages on release), short README, `config.example.toml`, CHANGELOG, release **1.0.0** | Docs reviewed; release tagged |

Each phase includes its tests and docstrings. The old code is deleted in phase 1.

## Decisions log

- One template and one server per fetch (stacked templates and multi-server fetches
  were dropped). A server choice is only ever a question (`select = "prompt"`).
  Groups fetched from several servers before this can still be read and updated,
  but not updated with `--rematch`.
- HDF5 is the first and default answer when zabgrab asks for a format (there's still
  no built-in `format`).
- Release version: 1.0.0.
- No built-in defaults for `from`, `to` or `format`: they come from the CLI, a
  template or `[defaults]`, else the user is asked (format is first inferred from the
  output file extension).
- Text items (value types 1, 2, 4) are supported in v1. Plotting them is an error.
- Text in `.mat` is always a cell array. Large volumes trigger a prompt
  (`--allow-large-text` skips it). Non-interactive runs continue with a warning.
- Append groups are a storage detail. Reading, plotting and exporting use the merged
  view by default.
- The untracked sample data files in the repo are out of scope.
- zabbix_utils was dropped in favour of a thin JSON-RPC client on aiohttp
  (phase 2). zabbix_utils closes its HTTP session after any API error, which breaks
  retries and concurrent requests, and silently reads `ZABBIX_TOKEN` and similar
  environment variables.
- `zabgrab login`/`logout` became `zabgrab token list|add|delete|reset`, done early
  (after phase 2). Tokens are stored in numbered keyring entries (`token1`,
  `token2`, ...), avoiding both a separate index and a single size-limited entry.
- Global options are accepted by every command, before or after it (a shared
  decorator whose callbacks update the click context object).
- API endpoint redirects (e.g. HTTP to HTTPS) are never followed automatically:
  that turns the POST into a GET and could leak credentials to another host.
  Interactive users are asked whether to use the new URL (with a warning if the host
  differs) and, for configured servers, whether to update the URL in the config file
  that defines the server. Non-interactive runs fail with the URL to use.
- A token stored under a URL that redirects is still used after following the
  redirect; interactive users are offered to move it to the new URL (non-interactive
  runs stop at the redirect itself, so never get this far).
  `token add` follows redirects the same way when checking a token, so it's stored
  under the final URL.
- Search patterns use Zabbix's own semantics (whole-value match ignoring case, `*`
  wildcard), so server-side limits are exact.

- Times: ISO 8601 is parsed exactly first (dateparser alone, with day-first order,
  reads `2026-10-01` as 10 January); everything else goes to
  dateparser, day-first.
- Without `--output`, interactive runs ask whether to save under the automatic name:
  `y`, `n`, or `o` to type another path, then handled as if given with `--output`
  (keeping any format just asked for).
- `--hline`/`--vline` rather than `--trendline`, which suggests a fitted line;
  they stay on `plot`, keeping `fetch --plot` a quick look. Each marks its own
  axis in that axis's terms, so on histograms `--hline` is a count, showing which
  bins exceed a limit, and `--vline` a value. (Sideways histograms, keeping
  values vertical everywhere, were tried and dropped: bins read best along the
  bottom.) `--log-y` became `--log`, since plots aren't only of values over
  time.
- `--bin` is shared by `show` and `plot` rather than being a command of its own,
  so it combines with their selection options; equal-width labelled bars beat
  proportional ones for open-ended and logarithmic edges.
- `show` is one command for files and fetched data, rather than adding sorting and
  filtering to `fetch`; `fetch --show` only lists. Sorting by value is per item, in
  sections, rather than one ranking across items in different units. `--grep` is a
  regular expression (Python's, close to `grep -E`), since log searches need
  alternatives and anchors; a pager rather than an interactive viewer avoids a
  heavy dependency.
- Overlapping groups aren't warned about when reading: overlap is often deliberate,
  is merged away, and a warning on every `plot`/`export` couldn't be dealt with short
  of flattening the file. Instead `fetch --append` notes it once, when it's added,
  and `info --overlaps` shows it on request.
- `--output` (and `export`/`plot`'s `-o`) can be repeated, to save several formats
  from one fetch rather than fetching again or exporting afterwards.
- Non-interactive runs never write files unasked: without `--output`, they warn that
  the data wasn't saved. An existing explicit output file is saved alongside under a
  new name, with a warning (`--overwrite` replaces it).
- Raw downloads are chunked by day and trends by 30 days; network errors are retried
  three times with backoff; each item downloads its chunks in order, `jobs` items at
  a time.
- `--save-template` saves a self-contained template (the template used, the command
  line and prompt answers; never `[defaults]` or built-in values), rather than only
  the command line's values, which would leave a template built on another one
  without its items.
- The "Next time" command is the command line as typed, with answered prompts added
  as options (replacing any given). Answers it can't express (items narrowed from a
  template, several formats) are noted instead.

- `.mat`/`.h5` files are written with HDF5's earliest file format so that MATLAB's
  older HDF5 library can read them. The layout passed `tests/matlab/check_files.m`
  in MATLAB R2023b and R2026b; the verified files are kept in
  `tests/matlab/reference/` and the test suite checks new files match them exactly.
- `mat_text_warn_entries` defaults to 200,000: MATLAB loads text quickly (about
  100,000-190,000 entries a second), so the real costs are size (480 bytes each) and
  write time (60 µs each).
- Updates of raw data start immediately after the previous group's end (not 1 s
  later), since raw values have sub-second times.
- Items in a group are named `item_<id>`, with `_2`, `_3`, ... when items on
  different servers share an ID; readers use the stored server and ID, not the names.
- CI moved from GitLab to GitHub Actions, running on every push and on pull
  requests from forks. It also builds the documentation; a separate workflow
  publishes it to GitHub Pages on every push to `main` and every release, building
  the latest release's tag for the root and `main` for `/dev/` each time (a Pages
  deployment replaces the whole site). Release events re-run that workflow on
  `main`, since Pages only accepts deployments from the default branch.
  Releases are prepared on a `release/X.Y.Z` branch (changelog date, last fixes),
  tagged by hand there (setuptools_scm takes the version from the tag) and merged
  into `main` with a merge commit, so `main` contains the tag.
- Publishing a GitHub release uploads it to PyPI (`publish.yml`), with trusted
  publishing rather than a stored API token. The build checks its files are named
  for the tag's version before uploading, since PyPI never takes a version twice.
  Run by hand, the workflow uploads a development build (without the local
  `+g...` suffix, which PyPI refuses) to TestPyPI, to try the setup out first.
- Tests run in UK time, which their expected local times are written in. Windows
  can't change a running process's time zone, so there the system time zone must
  be UK time (CI sets it with `tzutil`), and the tests stop with an error otherwise.
- Files written via a temporary file get normal permissions (the umask, or the
  replaced file's), not the temporary file's private 0600.

- Code is commented wherever it isn't trivial, and every module, class, function and
  method has a NumPy-style docstring (Sphinx/napoleon sections such as `Parameters`,
  `Returns`, `Raises`), enforced by ruff's `D` rules; docstring examples run as
  doctests.

- Storage periods come from the item settings (`history`, `trends`); a server-wide
  housekeeping override isn't visible through the API, so retention warnings say
  data *may* have been deleted, and fetches and updates still go ahead.

- With one template per fetch, the `output_pattern` placeholder `{templates}` became
  `{template}` (before 1.0.0, so with no compatibility alias). The metadata keeps
  `query.templates` as a list and `templates` as a table, so the file layout is
  unchanged.
- `update --server` refuses groups fetched from several servers, as `--rematch`
  does: item IDs only mean something on the server they came from.
- `~` in `output_dir` means the home directory.

- Each `.mat`/`.h5` group's metadata records the file layout version
  (`zabgrab.format_version`, `FORMAT_VERSION` in `model/series.py`). Readers (the
  Python reader and `zabgrab_load.m`) refuse groups with a newer version or none,
  and appending refuses files with such groups. It's per group, not a file
  attribute, since files grow by appending and adding a file attribute would have
  changed the MATLAB-verified layout. Increase it for changes older readers would
  misread; additive changes don't need it.

- `--item` means the same everywhere: an item spec (it used to be a whole-text glob
  for `plot` and `export`, which silently matched nothing for specs like
  `name=*0.3*`). That glob is now `--match`.
- Items' Zabbix tags are stored in each item's metadata entry (`items[].tags`), so
  `tag=` specs work on files. It's an addition older readers ignore, so
  `FORMAT_VERSION` stays 1; tags are as of the fetch, and merged views use the
  latest group's.

## Follow-up tasks

None at present.

## Open questions

None at present.

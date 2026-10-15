# zabgrab

A command line tool for fetching data from Zabbix, rewritten for 1.0.0 in phases.
[PLAN.md](PLAN.md) holds the design, the phases and the decisions log: read it before
starting work, and keep it in sync when the design changes. Development happens in
feature branches, merged into `main` through pull requests.

## Setup and checks

Use a virtual environment in the repo:

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev,plot,docs]"
.venv/bin/pre-commit install
```

Run all of these before committing:

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest --cov=zabgrab
```

and, when the docs change, `.venv/bin/sphinx-build --fail-on-warning -b html docs
docs/_build/html`.

CI (`.github/workflows/ci.yml`) runs the same checks on Linux (Python 3.11 to 3.14),
macOS and Windows, fails below 100% coverage, and builds the docs.
`.github/workflows/docs.yml` publishes the docs to GitHub Pages for each release.

## Working conventions

- **Commit as you go:** commit each self-contained change as soon as it passes the
  checks, without asking first, with a descriptive message.
- **100% test coverage:** add tests that cover every branch of new code. Use
  `# pragma: no cover` only for lines that genuinely can't be tested.
- **Comments:** comment code wherever it isn't trivial, explaining why rather than
  restating what.
- **Docstrings:** every module, class, function and method gets a NumPy-style
  docstring that Sphinx (napoleon) can render: a one-line summary, then the sections
  that apply (`Parameters`, `Returns`, `Yields`, `Raises`, `Attributes`, `Examples`)
  with `-----` underlines, and the summary in the imperative mood ("Make a table",
  not "A table"). Private helpers, nested functions and methods implementing a
  documented protocol may have just a one-line summary; `__init__` parameters go in
  the class docstring. Click commands' docstrings are their `--help` text, so they
  describe usage instead. ruff's `D` rules (numpy convention) enforce the basics,
  and examples in docstrings run as doctests.

- **Docs:** user-visible changes update the Sphinx docs (`docs/`, usage-focused;
  the command reference is generated from the click help) and `CHANGELOG.rst`.
  `docs/developers/releasing.rst` describes how to make a release.

## Architecture

- `model/`: data classes only. `services/`: the logic. `adapters/`: Zabbix, config
  files, file formats, keyring. `ui/`: rendering. `cli/`: click wiring only.
- Services get their dependencies (Zabbix client, UI, clock, ...) as arguments and
  never print or prompt directly; they go through the `UI` port, whose questions
  always carry a `fix` for non-interactive runs.
- Data goes to stdout; messages, warnings and progress go to stderr.

## Tests

- Tests run against an in-memory fake Zabbix (`tests/fakezabbix.py`) and fake UI,
  clock and keyring (`tests/fakes.py`); CLI tests use `tests/cli_support.py`.
- Tests run in UK time (expected local times are written in it). On Windows, which
  can't change a running process's time zone, the system time zone must be UK time.
- `tests/test_matlab.py` runs `tests/matlab/check_files.m` in MATLAB when it's
  installed (or listed in `ZABGRAB_MATLAB`), and is skipped otherwise. The `.mat`/`.h5`
  layout is also pinned by MATLAB-verified files in `tests/matlab/reference/`; if a
  change breaks those tests, the new layout must be re-checked in MATLAB first.

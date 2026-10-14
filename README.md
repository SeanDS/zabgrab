# Zabgrab
Command line tool for Zabbix data retrieval.

> **Zabgrab is being rewritten for version 1.0.0.** See [PLAN.md](PLAN.md) for the
> design. Only the configuration commands below work so far; data retrieval returns in
> a later phase. The full documentation will be written before the release.

## Installation
Requires Python 3.11 or later.

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

## Credits
Sean Leavey <sean.leavey@stfc.ac.uk>

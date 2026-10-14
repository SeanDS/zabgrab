# Zabgrab
Command line tool for Zabbix data retrieval.

> **Zabgrab is being rewritten for version 1.0.0.** See [PLAN.md](PLAN.md) for the
> design. Configuration and finding hosts/items work so far; data retrieval returns
> in a later phase. The full documentation will be written before the release.

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

## Credits
Sean Leavey <sean.leavey@stfc.ac.uk>

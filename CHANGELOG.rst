Changelog
=========

1.0.0 (unreleased)
------------------

Complete rewrite; not compatible with earlier versions' command line or
configuration. See the documentation at https://seands.github.io/zabgrab/.

- Requires Python 3.11+ and Zabbix 7.0+.
- New layered TOML configuration (system, user, project, explicit) with validation
  errors that point at the offending file and key.
- New ``zabgrab config paths|show|edit`` commands.
- New ``zabgrab hosts`` and ``zabgrab items`` commands to find hosts and items.
  Items are sorted by item ID, or by any column with ``items --sort``.
- Multiple servers can be configured; each fetch uses one, chosen with ``--server``,
  the template or ``[defaults]``, or at a prompt (custom URLs can be saved to the
  config).
- API tokens are read from an environment variable or the system keyring, or pasted
  at a prompt; username/password login is also supported.
- New ``zabgrab token list|add|delete|reset`` commands to manage tokens in the
  keyring.
- Global options (``-v``, ``-q``, ``--debug``, ``--config``, ...) work before or
  after the command.
- New ``zabgrab fetch`` command: a template and/or item specs, parallel
  downloads with progress bars, raw history or hourly trends, every item value
  type, TSV/CSV output with metadata, automatic file names, ``--explain`` and
  ``--dry-run``. ``--agg`` applies to hourly trends only; with raw data it's ignored
  with a warning.
- MATLAB v7.3 (``.mat``) and HDF5 (``.h5``) output, holding one group per fetch;
  ``fetch --append`` adds groups to existing files.
- ``fetch --save-template NAME`` saves a fetch, with any answers to questions, as a
  template (``--save-to`` picks the config file; ``--force`` replaces a template). After
  questions, ``fetch`` shows the equivalent command for next time.
- New ``zabgrab update FILE`` command to add the data since a file's last fetch.
- New ``zabgrab export FILE`` command to merge a file's groups into a new file,
  optionally converting, filtering items and times.
- New MATLAB function ``matlab/zabgrab_load.m`` to load a file's merged data.
- New ``zabgrab plot FILE`` command and ``fetch --plot`` option (with the ``plot``
  extra) to plot data, merged across groups, showing or saving the figure;
  ``--log-y`` uses log scales.
- ``fetch`` and ``update`` warn when the requested range starts before an item's
  history (or, for hourly trends, trend) storage period, since older data may have
  been deleted.
- ``.mat`` and ``.h5`` files record their layout's version in each group's
  metadata (``zabgrab.format_version``). zabgrab and ``zabgrab_load.m`` refuse
  groups written with a newer layout, rather than misread them, and zabgrab won't
  append to their files.
- When ``fetch`` asks whether to save the data under its automatic name, ``o``
  lets you type another path instead.
- New ``zabgrab info FILE`` command listing a file's items (or, with ``--groups``,
  its fetch groups), with the same selection options as ``plot`` and ``export``.
- ``plot``, ``export`` and ``info`` select items with ``--item`` item specs, as
  ``fetch`` does, matching exactly as the Zabbix API would (e.g. ``--item
  'host=lab1,name=*0.3*'``), and with ``--match`` ``fnmatch`` patterns (``*``,
  ``?``, ``[seq]``) matched against each item's whole ID, name, key or
  ``host: name``.
  When nothing matches, they suggest ``zabgrab info``.
- Fetched files store each item's Zabbix tags, so ``--item 'tag=...'`` works on
  them too.
- New documentation, built with Sphinx, including an example configuration
  (``config.example.toml``).
- Fixed: the old CSV output's columns shifted when timeseries had different
  lengths.

0.1.2 (2023-12-12)
------------------

- Changed CSV output format to display retrieved timeseries in parallel columns instead
  of sequentially.
- Avoid the user's editor complaining that the config file doesn't exist.
- Added ``--no-ssl-verify`` flag to switch off SSL certificate verification.

0.1.1 (2023-03-15)
------------------

- First packaged release. No other changes.

0.1.0 (2023-02-08)
------------------

- Initial release.

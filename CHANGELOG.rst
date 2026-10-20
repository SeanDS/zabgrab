Changelog
=========

1.1.0 (unreleased)
------------------

- New ``zabgrab show`` command listing data points, from a file or fetched from
  Zabbix: in time order, or each item's by value (``--sort``, ``--desc``), sliced
  with ``--limit`` and ``--offset``, filtered with ``--grep`` (a regular
  expression), ``--above`` and ``--below``, and paged in a terminal. Useful for
  text and log items, which can't be plotted, and for finding the highest values.
- New ``fetch --show`` option to list the fetched data.
- New ``--bin`` option for ``show`` and ``plot``, counting each item's values in
  bins with the given edges (e.g. ``--bin 10,100,1000``): ``show`` lists each
  bin's count and share, and ``plot`` draws a histogram.
- New ``plot --hline`` and ``--vline`` options to draw reference lines, such as
  limits or events: ``--hline 'Max value=500'`` at a value, optionally only on
  the plots of items with given units (``--hline 'Max=26 °C'``), and ``--vline
  'Filter change=2026-10-14 10:00'`` at a time. On histograms, ``--hline`` marks
  a count and ``--vline`` a value. Repeat them for several.
- ``plot --log-y`` and ``fetch --log-y`` are now ``--log``. ``--log-y`` still
  works, with a warning, until 2.0.

1.0.0 (2026-10-15)
------------------

Complete rewrite; not compatible with earlier versions' command line or
configuration. See the documentation at https://seands.github.io/zabgrab/.

**Requirements and setup**

- Requires Python 3.11+ and Zabbix 7.0+. Plotting is an optional extra
  (``pip install 'zabgrab[plot]'``).
- New layered TOML configuration (system, user, project, explicit) with validation
  errors that point at the offending file and key, and an example configuration
  (``config.example.toml``). New ``zabgrab config paths|show|edit`` commands.
- Multiple servers can be configured; each fetch uses one, chosen with ``--server``,
  the template or ``[defaults]``, or at a prompt (custom URLs can be saved to the
  config).
- API tokens are read from an environment variable or the system keyring, or pasted
  at a prompt; username/password login is also supported. New ``zabgrab token
  list|add|delete|reset`` commands manage tokens in the keyring.
- Global options (``-v``, ``-q``, ``--debug``, ``--config``, ``--isolated``,
  ``--no-input``, ...) work before or after the command.
- Anything missing is asked for, and afterwards zabgrab shows the equivalent
  command for next time. Scripts and scheduled jobs never get a prompt
  (``--no-input``, ``$ZABGRAB_NO_INPUT`` or ``$CI``, or no terminal): questions
  with a safe answer get it, with a warning, and the others stop with
  instructions. zabgrab exits with status 3 when only some items could be
  fetched.

**Finding items**

- New ``zabgrab hosts`` and ``zabgrab items`` commands to find hosts and items,
  with patterns that work as in the Zabbix API. Items are sorted by item ID, or by
  any column with ``items --sort``.

**Fetching**

- New ``zabgrab fetch`` command: a template and/or ``--item`` item specs, parallel
  downloads with progress bars, raw history or hourly trends, every item value
  type, ``--explain`` and ``--dry-run``. ``--agg`` applies to hourly trends only;
  with raw data it's ignored with a warning.
- Fetches matching more than ``max_items`` items (by default 50) ask before
  downloading them all (``--yes`` skips the question).
- ``fetch`` and ``update`` warn when the requested range starts before an item's
  history (or, for hourly trends, trend) storage period, since older data may have
  been deleted.
- Times are shown and read in local time, or the ``timezone`` setting; files
  store them in UTC.
- ``fetch --save-template NAME`` saves a fetch, with any answers to questions, as a
  template (``--save-to`` picks the config file; ``--force`` replaces a template).

**Saving**

- MATLAB v7.3 (``.mat``), HDF5 (``.h5``), TSV and CSV output, each with metadata
  recording exactly what was fetched, and automatic file names (``output_dir`` and
  ``output_pattern``).
- ``.mat`` and ``.h5`` files hold one group per fetch; ``fetch --append`` adds
  groups to existing files, and notes when the new data's range overlaps a group
  already in the file for the same items.
- ``-o`` can be given several times to ``fetch``, ``export`` and ``plot``, to save
  to several files at once, e.g. ``-o data.h5 -o data.csv`` or ``-o plot.png -o
  plot.pdf``.
- When ``fetch`` asks whether to save the data under its automatic name, ``o``
  lets you type another path instead.
- Saving many text values to a ``.mat`` file, which stores them inefficiently,
  warns and offers ``.h5`` instead (``--allow-large-text`` accepts it).
- ``.mat`` and ``.h5`` files record their layout's version in each group's
  metadata (``zabgrab.format_version``). zabgrab and ``zabgrab_load.m`` refuse
  groups written with a newer layout, rather than misread them, and zabgrab won't
  append to their files.
- Fetched files store each item's Zabbix tags, so ``--item 'tag=...'`` works on
  them too.

**Updating files**

- New ``zabgrab update FILE`` command to add the data since a file's last fetch,
  with the same items and settings. It writes through a safety copy
  (``--in-place`` skips it), ``--rematch`` picks up items added since, and items
  deleted from Zabbix are skipped with a warning naming them.

**Using files**

- New ``zabgrab info FILE`` command listing a file's items (or, with ``--groups``,
  its fetch groups; with ``--overlaps``, how many of each group's points another
  group also holds).
- New ``zabgrab plot FILE`` command and ``fetch --plot`` option (with the ``plot``
  extra) to plot data, merged across groups, showing or saving the figure;
  ``--log-y`` uses log scales.
- New ``zabgrab export FILE`` command to merge a file's groups into a new file,
  optionally converting, filtering items and times.
- ``plot``, ``export`` and ``info`` select items with ``--item`` item specs, as
  ``fetch`` does, matching exactly as the Zabbix API would (e.g. ``--item
  'host=lab1,name=*0.3*'``), and with ``--match`` ``fnmatch`` patterns (``*``,
  ``?``, ``[seq]``) matched against each item's whole ID, name, key or
  ``host: name``. When nothing matches, they suggest ``zabgrab info``.
- New MATLAB function ``matlab/zabgrab_load.m`` to load a file's merged data.

**Documentation and fixes**

- New documentation, built with Sphinx, at https://seands.github.io/zabgrab/.
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

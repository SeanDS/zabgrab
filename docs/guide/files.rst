Files zabgrab writes
====================

zabgrab saves data in four formats. Each file holds the data and metadata
describing exactly how it was fetched, so you can tell later what's in it, and
``zabgrab update`` can continue it.

======================= ======================================================
Format                  What it holds
======================= ======================================================
``.h5`` (HDF5)          A group per fetch: its metadata, and each item's
                        details and data. Can be added to.
``.mat`` (MATLAB v7.3)  The same layout as ``.h5``, stored the way MATLAB's
                        ``load`` reads it. Can be added to.
``.tsv``, ``.csv``      One fetch: the metadata as ``#`` comment lines, then a
                        table with columns for each item. Can't be added to.
======================= ======================================================

``zabgrab fetch`` writes all four (see :doc:`fetching`), ``zabgrab update`` adds to
``.h5`` and ``.mat`` files (see :doc:`updating`), and ``zabgrab export`` writes any
of them from an existing file (see :doc:`analysing`). :doc:`analysing` shows how to
load them in MATLAB and Python.

All times are stored in UTC: as POSIX seconds (seconds since 1970-01-01 00:00 UTC)
in ``.h5`` and ``.mat`` files, and as ISO 8601 text in the metadata. TSV and CSV
files are the exception: their data's times are ISO 8601 in your local time, or the
``timezone`` setting, with the UTC offset (e.g. ``2026-10-14T09:00:00+01:00``).

.. _file-layout:

HDF5 and MATLAB files
---------------------

Groups
~~~~~~

Each fetch is saved as a group (a MATLAB variable in ``.mat`` files), named
``fetch_0001``, ``fetch_0002``, ... in the order they were written, or as named with
``--group``. A new file has one group; ``fetch --append`` and ``update`` add a group
each, and leave the existing ones as they are. Groups can overlap in time (with
``fetch --append``) or follow on from each other (with ``update``); zabgrab merges
them when reading, keeping repeated points once (see ``zabgrab info --overlaps`` in
:doc:`analysing`).

.. code-block:: text

   /fetch_0001
       meta                  the metadata, as JSON text
       t_from, t_to          the time range fetched (POSIX seconds, UTC)
       items/
           item_41207/       one group per item, named after its ID
               itemid, hostid, value_type
               host, host_name, name, key, units, server, server_name
               time          N values, POSIX seconds (UTC)
               value         N values (raw data)
               min, avg, max, count                   (hourly trends instead)
               log_time, source, severity, event_id   (log items, as well)
           item_41208/ ...
   /fetch_0002 ...

``t_from`` and ``t_to`` are the time range asked for, which can be wider than the
data's: an item may have no values near the start or end, or none at all. Items with
the same ID from different servers (only possible in a file made by ``export``) are
named ``item_41207``, ``item_41207_2``, ...: read the ID from ``itemid``, not from the
group's name.

Item details
~~~~~~~~~~~~

Each item's group has its details as single values:

================= ======= ==========================================================
Field             Type    What it is
================= ======= ==========================================================
``itemid``        integer The Zabbix item ID.
``hostid``        integer The Zabbix host ID.
``value_type``    integer The Zabbix value type: 0 float, 1 character, 2 log,
                          3 unsigned integer, 4 text.
``host``          text    The host's technical name, e.g. ``lab1``.
``host_name``     text    The host's visible name, e.g. ``Lab 1``.
``name``          text    The item's name, e.g. ``Particles 0.3um``.
``key``           text    The item's key, e.g. ``particles[0.3]``.
``units``         text    The item's units, e.g. ``#/m3``, or empty.
``server``        text    The Zabbix server's URL.
``server_name``   text    The server's name in your config, or empty.
================= ======= ==========================================================

Columns
~~~~~~~

Then come the item's data, as columns of N values each (the same N for all of an
item's columns; N can be 0):

========================== ============== ================================================
Column                     Type           Holds
========================== ============== ================================================
``time``                   double         When each value was collected, in POSIX seconds
                                          (UTC), with fractions of a second where Zabbix
                                          recorded them.
``value``                  double         Raw values of float items.
``value``                  uint64         Raw values of unsigned integer items.
``value``                  text           Raw values of character, log and text items.
``min``, ``avg``, ``max``  double         Hourly trends: the statistics chosen with
                                          ``--agg`` (by default all three), for the hour
                                          starting at ``time``.
``count``                  int64          Hourly trends: how many values the hour had.
``log_time``               double         Log items: the time in the log entry itself
                                          (POSIX seconds), or NaN where Zabbix has none.
``source``                 text           Log items: the log's source, or empty.
``severity``               int64          Log items: the entry's severity (0 if none).
``event_id``               int64          Log items: the entry's event ID (0 if none).
========================== ============== ================================================

Values are sorted by time. Hourly trends are only fetched for numeric items, so a
group holds either raw data or trends (``query.resolution`` in the metadata says
which).

In ``.h5`` files, text is UTF-8 strings, numbers are 64-bit, columns are 1-D and
compressed with gzip, and single values are scalars.

What ``.mat`` files add
~~~~~~~~~~~~~~~~~~~~~~~

``.mat`` files are HDF5 files too, with what MATLAB's ``load`` needs:

- a 512-byte MATLAB v7.3 header at the start of the file;
- groups marked as MATLAB structs, so each fetch loads as a struct variable;
- text as MATLAB ``char`` arrays (UTF-16);
- columns stored so MATLAB sees N x 1 vectors, with text columns as cell arrays of
  ``char`` (their contents are in the ``#refs#`` group, as MATLAB itself stores
  them);
- single values as 1 x 1 ``double``.

Text columns make ``.mat`` files large and slow to load, so before a ``.mat`` file
holds more than ``mat_text_warn_entries`` text values (by default 200,000) zabgrab
asks whether to continue, save a ``.h5`` file instead, or leave the text items out.
If you continue (or pass ``--allow-large-text``), the file is marked, with a
``zabgrab_large_text_accepted`` attribute on its root, so that updates don't ask
again.

TSV and CSV files
-----------------

TSV (tab-separated) and CSV (comma-separated) files hold one fetch, in three parts
(here, a CSV file):

1. A first line saying what the file is, then the metadata as JSON, each line
   starting with ``#``.
2. A header row naming the columns.
3. The data rows.

.. code-block:: text

   # zabgrab data. The metadata below is JSON; data starts after it.
   # {
   #   "zabgrab": {
   #     "version": "1.1.1",
   #     "format_version": 1
   #   },
   #   ...
   # }
   lab1: Particles 0.3um time,lab1: Particles 0.3um value,lab1: Particles 0.5um time,lab1: Particles 0.5um value
   2026-10-14T09:00:00+01:00,1234.5,2026-10-14T09:00:30+01:00,201.0
   2026-10-14T09:01:00.250000+01:00,1180.0,,
   2026-10-14T09:02:00+01:00,5210.0,,

Each item has a column for each of its columns, as in ``.h5`` files (``time`` and
``value``, the trend statistics, or the log fields), named ``host: name column``. If
two items would get the same name, their IDs are added (``lab1: Uptime (41210)
value``), and if the file has items from several servers (only possible with
``export``), the server's name (or URL) comes first (``lab/lab1: Particles 0.3um
value``).

Items' columns are side by side, each item's values from the top, so a row's values
aren't necessarily from the same time: read each item's own ``time`` column. Items
with fewer values are padded with empty cells, and missing values (such as a log
entry without a log time) are empty too.

Times are ISO 8601, in your local time or the ``timezone`` setting, with the UTC
offset. Values containing the separator, quotes or line breaks are quoted, as in
other CSV files, so multi-line log entries need a CSV-aware reader such as pandas
(see :doc:`analysing`). The files are UTF-8.

.. _metadata:

Metadata
--------

Every fetch records the same metadata: as the ``meta`` text of its group in ``.h5``
and ``.mat`` files, and in the ``#`` lines of TSV and CSV files. It's JSON, so any
language can read it. This is the metadata of a group added by ``zabgrab update``:

.. code-block:: json

   {
     "zabgrab": {"version": "1.1.1", "format_version": 1},
     "created": "2026-10-15T09:00:02.417000+00:00",
     "command": ["zabgrab", "update", "particles.h5"],
     "query": {
       "templates": ["particles"],
       "custom": false,
       "from": "2026-10-14T09:00:00.000001+00:00",
       "to": "now",
       "start": "2026-10-14T09:00:00.000001+00:00",
       "end": "2026-10-15T09:00:02.417000+00:00",
       "timezone": "local",
       "resolution": "raw",
       "agg": ["min", "avg", "max"],
       "sources": {
         "from": "end of group 'fetch_0001'",
         "to": "now",
         "resolution": "group 'fetch_0001'",
         "agg": "group 'fetch_0001'",
         "timezone": "group 'fetch_0001'"
       },
       "requests": [
         {
           "label": "particles",
           "server": "lab",
           "items": [{"host": "lab1", "name": "Particles *"}]
         }
       ]
     },
     "templates": {
       "particles": {
         "server": "lab",
         "from": "1 day ago",
         "to": "now",
         "items": [{"host": "lab1", "name": "Particles *"}]
       }
     },
     "servers": [{"name": "lab", "url": "https://zabbix.example.org"}],
     "items": [
       {
         "server": "https://zabbix.example.org",
         "itemid": 41207,
         "host": "lab1",
         "host_name": "Lab 1",
         "name": "Particles 0.3um",
         "key": "particles[0.3]",
         "units": "#/m3",
         "value_type": "float",
         "tags": [{"tag": "area", "value": "cleanroom"}],
         "requests": ["particles"],
         "columns": ["time", "value"],
         "points": 2880
       }
     ],
     "skipped": [],
     "failed": [],
     "update_of": "fetch_0001"
   }

The keys are:

``zabgrab``
   The ``version`` of zabgrab that wrote the group, and the ``format_version`` of
   the file layout (see :ref:`format-versions`).
``created``
   When the fetch was made (UTC).
``command``
   The command line, as a list of arguments.
``query``
   What was asked for:

   ``templates``
      The template used, as a list (empty without one).
   ``custom``
      Whether the command line added items or changed what the template fetches
      (always true without a template).
   ``from``, ``to``
      The time range as given, e.g. ``"1 day ago"`` and ``"now"``. ``update``
      records the time it continued from: just after the end of the group before
      (a microsecond later, so no value is fetched twice).
   ``start``, ``end``
      The time range those meant when fetched (UTC).
   ``timezone``
      The ``timezone`` setting: ``"local"`` or a time zone name, e.g.
      ``"Europe/London"``.
   ``resolution``, ``agg``
      Raw data (``"raw"``) or hourly trends (``"1h"``), and the trend statistics
      chosen.
   ``sources``
      Where each setting came from, e.g. ``"command line (--from)"``, ``"template
      'particles'"``, ``"[defaults] in ~/.config/zabgrab/config.toml"`` or
      ``"built-in"``; for ``update``, the group it continued.
   ``requests``
      The item specifications searched for, and the server they were searched on:
      the template's, labelled with its name, and the command line's ``--item``
      options, labelled ``__custom``.
``templates``
   A copy of the template used, as it was in your config, so the fetch can be
   understood, and updated with ``--rematch``, without the config.
``servers``
   The servers fetched from: their names in your config, and URLs.
``items``
   Every item fetched: its details as in the file, its ``tags``, which ``requests``
   matched it, which ``columns`` were saved, and how many ``points``.
``skipped``
   Items that matched but weren't fetched, with the ``reason`` (e.g. binary items,
   or text items when fetching hourly trends, which Zabbix only keeps for numbers).
``failed``
   Items whose download failed, with the error as the ``reason``.
``update_of``
   In groups added by ``zabgrab update``: the group it continued from.
``export``
   Only in files written by ``zabgrab export``: the ``source`` file, the ``groups``
   read from it, the selection ``filters`` used (``items``, ``match``, ``from``,
   ``to`` and ``groups``), and the time ranges the data covers (``coverage``). The
   rest of an export's metadata is the last group's, with ``items``, ``start`` and
   ``end`` describing the exported data, so the export can be updated like the
   original.

The metadata never includes passwords, API tokens or other credentials. It doesn't
record who ran zabgrab, or on which computer, though the command line and paths in
it can give clues.

.. _format-versions:

Format versions
---------------

Each group's metadata records the version of the file layout it was written with,
as ``zabgrab.format_version`` (currently 1), next to the zabgrab version
(``zabgrab.version``). The layout stays the same within a format version, so scripts
reading the files can check it. New metadata keys can be added without changing the
format version, so scripts should ignore keys they don't know.

If a later zabgrab changes the layout, it increases the format version; older
versions of zabgrab and ``zabgrab_load`` then refuse those groups (and won't add to
their files), asking you to upgrade, rather than misread them.

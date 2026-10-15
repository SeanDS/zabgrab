Analysing the data
==================

What's in a file
----------------

``zabgrab info`` lists the items a ``.mat`` or ``.h5`` file holds, merged across its
fetch groups: each item's ID, host, name, key and units, whether it's raw data or
hourly trends, and how many points it has over what time range.

.. code-block:: bash

   zabgrab info particles.h5
   zabgrab info particles.h5 --groups       # the fetch groups instead
   zabgrab info particles.h5 --overlaps     # ... and the points they share

``--overlaps`` lists the groups with a *Repeated* column: how many of each group's
points another group also holds (the same item at the same time), as happens when
``fetch --append`` adds a range already in the file. ``plot``, ``export`` and
``zabgrab_load`` merge the groups, keeping each point once, so repeated points do no
harm, but they take up space; ``zabgrab export`` writes a copy of the file that
holds each point once.

.. _choosing-items:

Choosing items, times and groups
--------------------------------

``zabgrab info``, ``plot`` and ``export`` choose what to use from a file the same way,
so ``info`` shows what ``plot`` or ``export`` would use:

``--item SPEC`` (``-i``)
   Items selected by an :ref:`item spec <item-specs>`, just as when fetching: an
   item ID (``12345``), a name pattern (``*0.3um``), or fields such as
   ``host=lab1,name=*0.3*,tag=area:lab``. It selects exactly what the same spec
   selects in ``zabgrab items`` and ``fetch``: patterns match the whole value,
   ignoring case, with ``*`` matching any text, and a tag must be one the item had
   when it was fetched. Repeat for several.
``--match PATTERN`` (``-m``)
   Items whose whole ID, name, key or ``host: name`` matches a pattern, ignoring
   case, with Python's `fnmatch <https://docs.python.org/3/library/fnmatch.html>`_
   wildcards: ``*`` matches any text, ``?`` any one character, ``[seq]`` any
   character in *seq* (e.g. ``[0-9]``), and ``[!seq]`` any character not in it.
   For example, ``--match "lab1: *0.3um"`` or ``--match "*[05].?um"``. Repeat for
   several.
``--from TIME``, ``--to TIME``
   A time range.
``--group NAME``
   Only some fetch groups (repeat for several).

Items are used if any ``--item`` or ``--match`` selects them, or if neither is
given.

.. code-block:: bash

   zabgrab info particles.h5 --item "host=lab1,name=*0.3*"
   zabgrab plot particles.h5 --item "*0.3um" --from "2 days ago"
   zabgrab export particles.h5 -o recent.csv --match "*0.5um" --from "1 week ago"

If nothing matches, ``plot`` and ``export`` say so, and suggest ``zabgrab info`` to
see what the file holds.

Plotting
--------

With the ``plot`` extra installed (``pip install 'zabgrab[plot]'``), ``zabgrab plot``
shows a file's data in a window, or saves the figure:

.. code-block:: bash

   zabgrab plot particles.h5
   zabgrab plot particles.h5 -o particles.png        # or .pdf, .svg, ...
   zabgrab plot particles.h5 -o plot.png -o plot.pdf # several formats
   zabgrab plot particles.h5 --item "*0.3um" --from "2 days ago"
   zabgrab plot particles.h5 --log-y                 # log scales

All of a file's groups are plotted as one dataset. Items with the same units share an
axis, and times are shown in your local time (or the ``timezone`` setting). Hourly
trends are drawn as the average, with the minimum and maximum as a shaded band. Lines
only break where no fetch covered the time, so sparse data and appended groups join
up, and gaps show where data is missing. Text items can't be plotted.

Choose what to plot with ``--item``, ``--match``, ``--from``, ``--to`` and
``--group`` (see :ref:`choosing-items`).

``zabgrab fetch --plot`` plots data as it's fetched (see :doc:`fetching`).

MATLAB
------

``.mat`` files load with MATLAB's ``load``. Each fetch group is a variable holding a
struct per item, named ``item_<ID>``:

.. code-block:: matlab

   S = load('particles.mat');
   item = S.fetch_0001.items.item_41207;
   item.name, item.units          % the item's details
   item.time                      % N x 1 POSIX times (seconds since 1970, UTC)
   item.value                     % N x 1 values
   t = datetime(item.time, 'ConvertFrom', 'posixtime', 'TimeZone', 'UTC');

For files updated many times, it's easier to load every group at once, merged, with
``zabgrab_load``. Download `zabgrab_load.m
<https://github.com/SeanDS/zabgrab/blob/main/matlab/zabgrab_load.m>`_ and put it on
your MATLAB path; it reads ``.mat`` and ``.h5`` files:

.. code-block:: matlab

   S = zabgrab_load('particles.h5');      % a struct array, one element per item
   plot(S(1).time, S(1).value)            % time is a UTC datetime
   title(sprintf('%s: %s [%s]', S(1).host, S(1).name, S(1).units))

Each element has the item's details (``itemid``, ``host``, ``name``, ``key``,
``units``, ...), ``time``, and its values: ``value`` for raw data, and ``min``,
``avg``, ``max`` and ``count`` for hourly trends. Log items also have ``log_time``,
``source``, ``severity`` and ``event_id``. Text values are cell arrays of char.

``.h5`` files can also be read with MATLAB's ``h5read``:

.. code-block:: matlab

   value = h5read('particles.h5', '/fetch_0001/items/item_41207/value');

Python
------

``.h5`` files are plain HDF5, readable with `h5py <https://www.h5py.org/>`_:

.. code-block:: python

   import json

   import h5py
   import pandas as pd

   with h5py.File("particles.h5") as file:
       for name, group in file.items():               # fetch_0001, fetch_0002, ...
           metadata = json.loads(group["meta"][()])
           for item in group["items"].values():
               label = f"{item['host'].asstr()[()]}: {item['name'].asstr()[()]}"
               times = pd.to_datetime(item["time"][:], unit="s", utc=True)
               values = item["value"][:]

Text values are stored as UTF-8 strings (use ``.asstr()`` to read them as Python
strings).

TSV and CSV files start with the metadata as ``#`` comment lines, then a header row,
then the data: a time column and a value column for each item (or ``min``, ``avg``,
``max`` and ``count`` columns for hourly trends), named after the item, as in
``lab1: Particles 0.3um time``. Times are ISO 8601, in local time (or the
``timezone`` setting). Read them with pandas:

.. code-block:: python

   import pandas as pd

   data = pd.read_csv("particles.tsv", sep="\t", comment="#")

Items have different numbers of values, so shorter columns are padded with empty
cells. Log entries can contain newlines, which pandas handles but simpler readers
such as ``numpy.loadtxt`` don't.

File layout
-----------

``.h5`` and ``.mat`` files share one layout. Each fetch is a group:

.. code-block:: text

   /fetch_0001
       meta                  the metadata, as JSON text
       t_from, t_to          the time range fetched (POSIX seconds, UTC)
       items/
           item_41207/
               itemid, hostid, host, host_name, name, key, units, value_type,
               server, server_name
               time          N values, POSIX seconds (UTC)
               value         N values (raw data)
               min, avg, max, count            (hourly trends)
               log_time, source, severity, event_id   (log items)
           item_41208/ ...
   /fetch_0002 ...

The metadata records the zabgrab version, when and how the fetch was made (the
command line), the server, the template and item specs, every matched item, the
time range, resolution and statistics, and where each setting came from. It never
includes passwords or tokens.

Each group's metadata also records the version of the file layout it was written
with, as ``zabgrab.format_version`` (currently 1), next to the zabgrab version
(``zabgrab.version``). The layout stays the same within a format version, so
scripts reading the files can check it. If a later zabgrab changes the layout, it
increases the format version; older versions of zabgrab and ``zabgrab_load`` then
refuse those groups (and won't add to their files), asking you to upgrade, rather
than misread them.

Exporting and converting
------------------------

``zabgrab export`` writes a file's data, merged across its groups, to a new file. Use
it to flatten a file that's been updated many times, to convert between formats, or
to extract some items or a time range:

.. code-block:: bash

   zabgrab export particles.h5 -o particles.mat
   zabgrab export particles.h5 -o recent.csv --item "*0.3um" --from "2 days ago"
   zabgrab export particles.h5 -o flat.h5 -o flat.csv     # several files at once

It chooses items, times and groups as described in :ref:`choosing-items`.

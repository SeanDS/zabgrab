Analysing the data
==================

Plotting
--------

With the ``plot`` extra installed (``pip install 'zabgrab[plot]'``), ``zabgrab plot``
shows a file's data in a window, or saves the figure:

.. code-block:: bash

   zabgrab plot particles.h5
   zabgrab plot particles.h5 -o particles.png        # or .pdf, .svg, ...
   zabgrab plot particles.h5 --item "*0.3um" --from "2 days ago"
   zabgrab plot particles.h5 --log-y                 # log scales

All of a file's groups are plotted as one dataset. Items with the same units share an
axis, and times are shown in your local time (or the ``timezone`` setting). Hourly
trends are drawn as the average, with the minimum and maximum as a shaded band. Lines
only break where no fetch covered the time, so sparse data and appended groups join
up, and gaps show where data is missing. Text items can't be plotted.

Choose what to plot with:

``--item PATTERN`` (``-i``)
   Items whose ID, name, key or ``host: name`` matches (with ``*`` wildcards; repeat
   for several).
``--from TIME``, ``--to TIME``
   A time range.
``--group NAME``
   Only some fetch groups (repeat for several).

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

Exporting and converting
------------------------

``zabgrab export`` writes a file's data, merged across its groups, to a new file. Use
it to flatten a file that's been updated many times, to convert between formats, or
to extract some items or a time range:

.. code-block:: bash

   zabgrab export particles.h5 -o particles.mat
   zabgrab export particles.h5 -o recent.csv --item "*0.3um" --from "2 days ago"

``export`` takes the same ``--item``, ``--from``, ``--to`` and ``--group`` options as
``plot``.

Keeping files up to date
========================

Long-running measurements are easiest to analyse from one file that grows over time.
zabgrab's ``.h5`` and ``.mat`` files are built for this: each fetch is stored as a
**group**, and new data is added as a new group, without rewriting what's there.

Updating a file
---------------

Fetch the history you want once, then run ``zabgrab update`` whenever you want the
latest data:

.. code-block:: bash

   zabgrab fetch -t particles --from "30 days ago" --to now -o particles.h5

   # later, e.g. every morning
   zabgrab update particles.h5

``update`` repeats the file's last fetch, from where it ended up to now, with the
same server, items and settings, and adds the new data to the file as another group.
If there's nothing new, the file isn't changed.

- ``--to TIME`` fetches up to another time than now.
- ``--rematch`` looks the items up again from the original template and patterns,
  picking up items added since (a new host, say), instead of fetching the same item
  IDs as before.
- ``--server`` fetches from another server: the same Zabbix server at another
  address, say. It looks up the same item IDs there, so if the server was replaced
  and its items have new IDs, use ``zabgrab migrate`` instead (see
  :ref:`migrating`).
- ``--dry-run`` shows what would be fetched.

Items deleted from Zabbix since the last fetch are skipped, with a warning naming
each one; their data so far stays in the file. If none of the items are left,
``update`` stops with an error and doesn't change the file. Items you've retired
(see :ref:`retiring`) aren't fetched at all.

The file is updated through a copy that replaces the original only once it's
complete, so an interrupted update can't damage it. For very large files,
``--in-place`` skips the copy.

To run updates on a schedule, see :doc:`automation`.

Adding other fetches to a file
------------------------------

``fetch --append`` adds any fetch to an existing ``.h5`` or ``.mat`` file (or creates
it), e.g. data from other items, or a range you missed:

.. code-block:: bash

   zabgrab fetch -t particles --from 2026-09-01 --to 2026-09-02 -o particles.h5 --append
   zabgrab fetch -i 41500 --from "30 days ago" --to now -o particles.h5 --append --group humidity

Groups are named ``fetch_0001``, ``fetch_0002``, and so on, unless ``--group`` names
them.

If the new range overlaps a group already in the file that has some of the same
items, zabgrab notes it. That's often what you meant (re-fetching a range to fill a
gap, say), and the repeated points are only used once when the file is read; to see
how many there are, run ``zabgrab info FILE --overlaps``. To continue a file from
where it ends, without overlap, use ``zabgrab update`` instead.

.. _migrating:

When the server is replaced
---------------------------

zabgrab knows each item by its server and item ID. If your Zabbix server is
replaced by a new one without its history, the same items come back on the new
server with new IDs, and ``update`` can't continue the file: the old IDs mean
nothing (or something else) on the new server.

``zabgrab migrate`` moves a file to the new server. It finds each item that
``update`` fetches on the new server, by its host and key, shows what it found, and
after you confirm, rewrites the file with the new server and item IDs:

.. code-block:: text

   $ zabgrab migrate particles.h5 --server labnew
   ┏━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
   ┃ Item ID ┃ Item                  ┃ Change                         ┃
   ┡━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
   │   41207 │ lab1: Particles 0.3um │ → 90311 on 'labnew' (matched)  │
   │   41208 │ lab1: Particles 0.5um │ → 90312 on 'labnew' (matched)  │
   │   41230 │ lab2: Pump pressure   │ retire (not found on 'labnew') │
   └─────────┴───────────────────────┴────────────────────────────────┘
   Warning: 1 item will be retired: its data stays in the file, but update won't
   fetch any more (--unretire brings an item back).
   Rewrite particles.h5 (12 groups)? [y/n] (n): y
   Changed particles.h5: moved 2 items to 'labnew', retired 1.

   $ zabgrab update particles.h5      # now fetches from labnew

The data is untouched, and every group is rewritten with the new IDs, so the data
from before and after the migration reads as one series per item, in ``zabgrab
plot``, ``show`` and ``export``, ``zabgrab_load`` in MATLAB, and your own scripts.

- **Items the new server doesn't have are retired**: their data stays in the file,
  under the old server and ID, but ``update`` no longer fetches them. zabgrab
  warns and asks before retiring anything.
- **Giving IDs yourself:** if an item's host or key changed, so it isn't found,
  give its new ID with ``--map OLD=NEW`` (repeat it for several), or list them in a
  file with ``--map-file``: one old and new ID per line, separated by a comma, a
  tab or spaces, with ``#`` for comments. These win over the IDs found
  automatically. ``--no-match`` turns the automatic matching off, so only the IDs
  you give move (the rest are retired).
- ``--dry-run`` shows what would change, and ``--yes`` changes the file without
  asking (in scripts, it's needed).

.. code-block:: bash

   zabgrab migrate particles.h5 --server labnew --dry-run
   zabgrab migrate particles.h5 --server labnew --map 41230=95002
   zabgrab migrate particles.h5 --server labnew --map-file new-ids.csv --no-match

where ``new-ids.csv`` holds, say:

.. code-block:: text

   old,new
   # the pumps moved to lab3 on the new server
   41230,95002
   41231,95003

``--map`` without ``--server`` changes IDs on the same server, e.g. for items that
were deleted and recreated.

The file is rewritten through a copy that replaces the original only once it's
complete. Your config's template isn't changed: if it selects items by ID, zabgrab
says which to change.

Migrating again
~~~~~~~~~~~~~~~

A file can be migrated as often as servers change. Each item keeps a history of its
earlier servers and IDs, and the file a log of every migration, with when it
happened, in its metadata (see :ref:`metadata`). ``zabgrab info --migrations``
lists the log:

.. code-block:: text

   $ zabgrab info particles.h5 --migrations
   ┏━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
   ┃ When                    ┃ Item                  ┃ Before          ┃ After                           ┃
   ┡━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
   │ 2026-11-05 10:12:40 GMT │ lab1: Particles 0.3um │ 41207 on lab    │ 90311 on labnew                 │
   │ 2026-11-05 10:12:40 GMT │ lab1: Particles 0.5um │ 41208 on lab    │ 90312 on labnew                 │
   │ 2026-11-05 10:12:40 GMT │ lab2: Pump pressure   │ 41230 on lab    │ retired (not found on 'labnew') │
   │ 2027-03-02 09:00:11 GMT │ lab1: Particles 0.5um │ 90312 on labnew │ retired (--retire)              │
   └─────────────────────────┴───────────────────────┴─────────────────┴─────────────────────────────────┘

.. _retiring:

Retiring items
--------------

When a sensor is unplugged for good, its item usually stays in Zabbix, and
``update`` keeps fetching it, adding nothing. Retire it to stop that, without
changing anything else. Its data stays in the file, and ``plot``, ``show`` and
``export`` still use it, but ``zabgrab info`` leaves it out of the list of items,
noting how many items it hid (``info --retired`` lists them too, marked as
retired):

.. code-block:: text

   $ zabgrab migrate particles.h5 --retire 90312
   ┏━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
   ┃ Item ID ┃ Item                  ┃ Change            ┃
   ┡━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
   │   90312 │ lab1: Particles 0.5um │ retire (--retire) │
   └─────────┴───────────────────────┴───────────────────┘
   Warning: 1 item will be retired: its data stays in the file, but update won't
   fetch any more (--unretire brings an item back).
   Rewrite particles.h5 (12 groups)? [y/n] (n): y
   Changed particles.h5: retired 1.

Repeat ``--retire`` for several items. ``update --rematch`` leaves retired items out
too, even if the template's patterns still find them. If the sensor comes back,
``--unretire`` starts updating it again, from the end of the file:

.. code-block:: bash

   zabgrab migrate particles.h5 --unretire 90312 --yes

Working with the whole file
---------------------------

The groups are only a storage detail: ``zabgrab plot``, ``zabgrab export`` and the
MATLAB function ``zabgrab_load`` join every group's data into one continuous dataset
per item, sorted by time, with repeated points kept once. See :doc:`analysing`.

After many updates, ``zabgrab export`` can also flatten a file into a new one with a
single group:

.. code-block:: bash

   zabgrab export particles.h5 -o particles-flat.h5

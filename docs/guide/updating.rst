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
- ``--server`` fetches from another server, e.g. if the server moved.
- ``--dry-run`` shows what would be fetched.

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

Working with the whole file
---------------------------

The groups are only a storage detail: ``zabgrab plot``, ``zabgrab export`` and the
MATLAB function ``zabgrab_load`` join every group's data into one continuous dataset
per item, sorted by time, with repeated points kept once. See :doc:`analysing`.

After many updates, ``zabgrab export`` can also flatten a file into a new one with a
single group:

.. code-block:: bash

   zabgrab export particles.h5 -o particles-flat.h5

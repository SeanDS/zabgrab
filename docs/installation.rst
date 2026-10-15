Installation
============

zabgrab needs Python 3.11 or later, and works with Zabbix 7.0 or later. Install it
with pip, ideally in a virtual environment, or with `pipx <https://pipx.pypa.io/>`_,
which keeps command line tools in their own environments:

.. code-block:: bash

   pip install zabgrab
   # or
   pipx install zabgrab

To plot data, install the ``plot`` extra, which adds matplotlib:

.. code-block:: bash

   pip install 'zabgrab[plot]'
   # or
   pipx install 'zabgrab[plot]'

Check it works:

.. code-block:: console

   $ zabgrab --version
   zabgrab, version 1.0.0

To install the latest development version instead, install from GitHub:

.. code-block:: bash

   pip install 'zabgrab[plot] @ git+https://github.com/SeanDS/zabgrab.git'

API tokens are kept in your system's keyring (Keychain on macOS, the Windows
Credential Locker, or the Secret Service on Linux desktops). Where there's no keyring,
such as on a headless server, zabgrab can read tokens from environment variables
instead (see :ref:`logging-in`).

For loading zabgrab's ``.mat`` and ``.h5`` files in MATLAB as one dataset, download
`zabgrab_load.m <https://github.com/SeanDS/zabgrab/blob/main/matlab/zabgrab_load.m>`_
and put it on your MATLAB path (see :doc:`guide/analysing`).

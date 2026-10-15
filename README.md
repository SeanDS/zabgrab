# zabgrab

zabgrab is a command line tool that fetches data from [Zabbix](https://www.zabbix.com/)
servers into files you can analyse: MATLAB (`.mat`), HDF5 (`.h5`), TSV and CSV.

**Documentation: https://seands.github.io/zabgrab/** (development version:
https://seands.github.io/zabgrab/dev/)

## Installation

zabgrab needs Python 3.11 or later, and works with Zabbix 7.0 or later.

```bash
pip install zabgrab
pip install 'zabgrab[plot]'    # with plotting (matplotlib)
```

[pipx](https://pipx.pypa.io/) (`pipx install 'zabgrab[plot]'`) keeps it in its own
environment.

## Running zabgrab

```bash
zabgrab --help                 # the commands
zabgrab COMMAND --help         # a command's options

zabgrab token add https://zabbix.example.org      # store an API token
zabgrab items "Particles *" --server https://zabbix.example.org
zabgrab fetch --server https://zabbix.example.org --item "host=lab1,name=Particles *" \
    --from "24 hours ago" --to now --output particles.h5
zabgrab plot particles.h5
```

zabgrab asks for anything it needs that you don't give it. The
[documentation](https://seands.github.io/zabgrab/) explains how to configure servers
and templates, keep files up to date, and load the data in MATLAB and Python.

## Building the documentation

The documentation is in `docs/`, built with [Sphinx](https://www.sphinx-doc.org/):

```bash
pip install -e ".[docs]"
sphinx-build -b html docs docs/_build/html
```

Then open `docs/_build/html/index.html`. The documentation's developer guide explains
how to set up a development environment, run the tests, and make a release.

## Credits

Sean Leavey <sean.leavey@stfc.ac.uk>

"""``zabgrab info``: what a file holds."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import click

from ..adapters.formats import hdf5
from ..model.item import ItemSpec
from ..services.dataset import Dataset, Group, filter_dataset, merge, select_groups
from ..ui.render import file_groups_table, file_items_table
from .app import AppContext, global_options, pass_app
from .reading import check_readable, selection_options, time_range


@click.command("info")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@selection_options
@click.option(
    "--groups",
    "by_group",
    is_flag=True,
    help="List the fetch groups instead of the items.",
)
@global_options
@pass_app
def info_command(
    app: AppContext,
    file: Path,
    items: tuple[ItemSpec, ...],
    match: tuple[str, ...],
    from_: str | None,
    to: str | None,
    groups: tuple[str, ...],
    by_group: bool,
) -> None:
    """List the items in FILE (.mat or .h5).

    Shows each item's ID, host, name, key, units, resolution, number of points and
    time range, merged across the file's fetch groups. --item, --match, --from,
    --to and --group select as for plot and export, so this shows what they would
    select. With --groups, lists the fetch groups instead.
    """
    check_readable(file, ("read", "read"))
    start, end, tz = time_range(app, from_, to)
    selected = select_groups(hdf5.read_fetches(file), groups)

    def narrow(group_list: Sequence[Group]) -> Dataset:
        """Merge groups and apply the item and time selection."""
        return filter_dataset(
            merge(group_list, app.ui), items=items, patterns=match, start=start, end=end
        )

    if by_group:
        rows = []
        for group in selected:
            data = narrow([group])
            rows.append(
                (
                    group.name,
                    group.metadata.get("created"),
                    group.t_from,
                    group.t_to,
                    len(data.series),
                    sum(len(one.columns["time"]) for one in data.series),
                )
            )
        app.ui.print_data(file_groups_table(rows, tz))
        return

    dataset = narrow(selected)
    if not dataset.series:
        app.ui.info(f"No items in {file} match the selection.")
        return
    app.ui.print_data(file_items_table(dataset.series, tz))

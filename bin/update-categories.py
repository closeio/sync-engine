#!/usr/bin/env python


import click

from inbox.error_handling import maybe_enable_error_reporting
from inbox.ignition import engine_manager


@click.command()
@click.option("--shard_id", type=int)
def main(shard_id) -> None:  # type: ignore[no-untyped-def]
    maybe_enable_error_reporting()

    if shard_id is not None:
        update_categories_for_shard(shard_id)
        update_folders_and_labels_for_shard(shard_id)
    else:
        for key in engine_manager.engines:
            update_categories_for_shard(key)
            update_folders_and_labels_for_shard(key)


def update_categories_for_shard(  # type: ignore[no-untyped-def]
    shard_id,
) -> None:
    print(f"Updating categories for shard {shard_id}")

    engine = engine_manager.engines[shard_id]

    query = "UPDATE category SET name='' WHERE name is NULL;"
    engine.execute(query)

    print(f"Updated names, updating deleted_at for shard {shard_id}")

    query = "UPDATE category SET deleted_at='1970-01-01 00:00:00' WHERE deleted_at is NULL;"
    engine.execute(query)


def update_folders_and_labels_for_shard(  # type: ignore[no-untyped-def]
    shard_id,
) -> None:
    print(f"Updating folders for shard {shard_id}")

    engine = engine_manager.engines[shard_id]

    query = "UPDATE folder SET canonical_name='' WHERE canonical_name is NULL;"
    engine.execute(query)

    print(f"Updated folders, updating labels for shard {shard_id}")

    query = "UPDATE label SET canonical_name='' WHERE canonical_name is NULL;"
    engine.execute(query)


if __name__ == "__main__":
    main()

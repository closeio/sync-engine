#!/usr/bin/env python
from collections.abc import Iterable

from gevent import monkey

monkey.patch_all()

import datetime
import logging

import click
from sqlalchemy.orm import Query, joinedload

from inbox.logging import configure_logging, get_logger
from inbox.models.block import Block
from inbox.models.session import global_session_scope
from inbox.util import blockstore

configure_logging(logging.ERROR)
log = get_logger()


BATCH_SIZE = 1000


def find_blocks(
    limit: "int | None",
    after: "datetime.datetime | None",
    before: "datetime.datetime | None",
) -> "Iterable[tuple[Block, int]]":
    query = (
        Query([Block])
        .options(joinedload(Block.parts))
        .filter(Block.size > 0)
        .order_by(Block.id)
    )

    if after:
        query = query.filter(Block.updated_at >= after)
    if before:
        query = query.filter(Block.updated_at < before)

    with global_session_scope() as db_session:
        count_query = query.with_session(db_session)
        if limit is not None:
            count_query = count_query.limit(limit)

        count = count_query.count()

    yielded = 0
    last_id = 0

    while True:
        with global_session_scope() as db_session:
            block_batch = (
                query.filter(Block.id > last_id)
                .limit(min(limit, BATCH_SIZE) if limit is not None else BATCH_SIZE)
                .with_session(db_session)
                .all()
            )

        if not block_batch:
            return

        for block in block_batch:
            if limit is not None and yielded >= limit:
                return

            yield block, count
            yielded += 1

        last_id = block_batch[-1].id


@click.command()
@click.option("--limit", type=int, default=None)
@click.option("--after", type=str, default=None)
@click.option("--before", type=str, default=None)
@click.option("--dry-run/--no-dry-run", default=True)
def run(
    limit: "int | None", after: "str | None", before: "str | None", dry_run: bool
) -> None:
    blocks = find_blocks(
        limit,
        datetime.datetime.fromisoformat(after) if after else None,
        datetime.datetime.fromisoformat(before) if before else None,
    )

    for block, count in blocks:
        data = blockstore.get_from_blockstore(block.data_sha256)
        if data is None:
            action = "not-present"
        else:
            action = "delete" if not dry_run else "would-delete"

        print(
            f"{block.id}/{count}",
            action,
            block.data_sha256,
            block.size if data else None,
            len(block.parts),
        )

        if action == "delete":
            blockstore.delete_from_blockstore([block.data_sha256])


if __name__ == "__main__":
    run()

#!/usr/bin/env python
from gevent import monkey

monkey.patch_all()

import datetime
import logging

import click
from sqlalchemy.orm import joinedload

from inbox.logging import configure_logging, get_logger
from inbox.models.block import Block
from inbox.models.session import global_session_scope
from inbox.util import blockstore

configure_logging(logging.ERROR)
log = get_logger()


@click.command()
@click.option("--limit", type=int, default=1000)
@click.option("--after", type=str, default="2024-08-19")
def run(limit: int, after: str) -> None:
    with global_session_scope() as db_session:
        blocks = (
            (
                db_session.query(Block)
                .options(joinedload(Block.parts))
                .filter(
                    Block.size > 0,
                    Block.updated_at >= datetime.datetime.fromisoformat(after),
                )
                .order_by(Block.updated_at)
            )
            .limit(limit)
            .all()
        )

    valid_blocks = {}
    for block in blocks:
        if block.data_sha256 in valid_blocks:
            continue

        data = blockstore.get_from_blockstore(block.data_sha256)
        print(
            block.id,
            block.data_sha256,
            block.filename or "no-filename",
            block.size if data else None,
            [(part.content_disposition, part.message_id) for part in block.parts],
        )

        if data:
            valid_blocks[block.data_sha256] = len(data)

    print(f"Total blocks: {len(blocks)}")
    print(
        f"Blocks with objects: {len(valid_blocks)}, size: {sum(valid_blocks.values())}"
    )


if __name__ == "__main__":
    run()

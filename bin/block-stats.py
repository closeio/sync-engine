#!/usr/bin/env python
from gevent import monkey

monkey.patch_all()

import datetime
import logging

import click

from inbox.logging import configure_logging, get_logger
from inbox.models.block import Block
from inbox.models.session import global_session_scope
from inbox.util import blockstore

configure_logging(logging.INFO)
log = get_logger()


@click.command()
@click.option("--limit", type=int, default=1000)
def run(limit: int) -> None:
    with global_session_scope() as db_session:
        blocks = (
            (
                db_session.query(Block)
                .filter(
                    Block.size > 0, Block.updated_at >= datetime.datetime(2024, 8, 19)
                )
                .order_by(Block.updated_at.desc())
            )
            .limit(limit)
            .all()
        )

    for block in blocks:
        data = blockstore.get_from_blockstore(block.data_sha256)
        print(block.data_sha256, block.filename, len(data) if data else None)


if __name__ == "__main__":
    run()

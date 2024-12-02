#!/usr/bin/env python
import datetime
import enum
import logging
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

import click
from sqlalchemy.orm import Query
from sqlalchemy.sql import func

from inbox.logging import configure_logging, get_logger
from inbox.models.block import Block
from inbox.models.session import global_session_scope
from inbox.util import blockstore

configure_logging(logging.ERROR)
log = get_logger()


DEFAULT_DELETE_BATCH_SIZE = 100
DEFAULT_BATCH_SIZE = 1000


class Resolution(enum.Enum):
    NOT_PRESENT = "not-present"
    DELETE = "delete"


def find_blocks(
    limit: "int | None",
    after: "datetime.datetime | None",
    before: "datetime.datetime | None",
    after_id: "int | None",
    before_id: "int | None",
    batch_size: int,
) -> "Iterable[tuple[Block, int]]":
    query = (
        Query([Block])
        .filter(Block.size > 0)  # empty blocks are not stored in S3
        .order_by(Block.id)
    )

    if after:
        query = query.filter(Block.created_at >= after)
    if before:
        query = query.filter(Block.created_at < before)
    if after_id:
        query = query.filter(Block.id >= after_id)
    if before_id:
        query = query.filter(Block.id < before_id)

    inner_max_id_query = query.with_entities(Block.id)
    if limit is not None:
        inner_max_id_query = inner_max_id_query.limit(limit)

    with global_session_scope() as db_session:
        max_id = db_session.query(func.max(inner_max_id_query.subquery().c.id)).scalar()

    offset = 0
    start_id = 1 if after_id is None else after_id

    while True:
        with global_session_scope() as db_session:
            block_batch = (
                query.filter(Block.id >= start_id)
                .limit(min(limit, batch_size) if limit is not None else batch_size)
                .with_session(db_session)
                .all()
            )

        if not block_batch:
            return

        seen_sha256s = set()
        for block in block_batch:
            if limit is not None and offset >= limit:
                return

            if block.data_sha256 not in seen_sha256s:
                yield block, max_id
                seen_sha256s.add(block.data_sha256)

            offset += 1  # noqa: SIM113

        start_id = block_batch[-1].id + 1


def delete_batch(delete_sha256s: "set[str]", dry_run: bool) -> None:
    if not delete_sha256s:
        return

    if not dry_run:
        blockstore.delete_from_blockstore(*delete_sha256s)
        print("deleted", len(delete_sha256s), "blobs")
    else:
        print("would-delete", len(delete_sha256s), "blobs")


@click.command()
@click.option("--limit", type=int, default=None)
@click.option("--after", type=str, default=None)
@click.option("--before", type=str, default=None)
@click.option("--after-id", type=int, default=None)
@click.option("--before-id", type=int, default=None)
@click.option("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
@click.option("--delete-batch-size", type=int, default=DEFAULT_DELETE_BATCH_SIZE)
@click.option("--repeat", type=int, default=1)
@click.option("--dry-run/--no-dry-run", default=True)
@click.option("--check-existence/--no-check-existence", default=False)
def run(
    limit: "int | None",
    after: "str | None",
    before: "str | None",
    after_id: "int | None",
    before_id: "int | None",
    batch_size: int,
    delete_batch_size: int,
    repeat: int,
    dry_run: bool,
    check_existence: bool,
) -> int:
    assert batch_size > 0
    assert delete_batch_size > 0

    delete_executor = ThreadPoolExecutor(max_workers=10)

    for repetition in range(repeat):
        blocks = find_blocks(
            limit,
            datetime.datetime.fromisoformat(after) if after else None,
            datetime.datetime.fromisoformat(before) if before else None,
            after_id,
            before_id,
            batch_size,
        )

        delete_sha256s = set()

        max_id = None
        for block, max_id in blocks:
            if check_existence:
                data = blockstore.get_from_blockstore(block.data_sha256)
            else:
                data = ...  # assume it exists, it's OK to delete non-existent data

            if data is None:
                resolution = Resolution.NOT_PRESENT
            else:
                resolution = Resolution.DELETE

            print_arguments = [
                f"{block.id}/{max_id}",
                block.created_at.date(),
                resolution.value,
                block.data_sha256,
                block.size if data else None,
            ]

            if repeat != 1:
                print_arguments.insert(0, repetition)

            print(*print_arguments)

            if resolution is Resolution.DELETE:
                delete_sha256s.add(block.data_sha256)

            if len(delete_sha256s) >= delete_batch_size:
                delete_executor.submit(delete_batch, delete_sha256s.copy(), dry_run)
                delete_sha256s.clear()

        delete_batch(delete_sha256s, dry_run)

        if max_id is None:
            return

        after_id = max_id + 1

    delete_executor.shutdown(wait=True)


if __name__ == "__main__":
    run()

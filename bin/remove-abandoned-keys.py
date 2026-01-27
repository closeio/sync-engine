#!/usr/bin/env python
from gevent import monkey

monkey.patch_all()

import signal
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

import click

from inbox import config
from inbox.models.message import Message
from inbox.models.session import global_session_scope
from inbox.util import blockstore


def find_keys(limit: "int | None", marker: "str | None") -> "Iterable[str]":
    bucket = blockstore.get_s3_bucket(
        config.config.get("TEMP_MESSAGE_STORE_BUCKET_NAME")
    )

    yielded = 0

    while True:
        all_keys = bucket.get_all_keys(marker=marker)
        for key in all_keys:
            yield key.name
            yielded += 1  # noqa: SIM113

            if limit is not None and yielded >= limit:
                return

        if not all_keys.is_truncated:
            return

        marker = all_keys[-1].name


def get_abandoned_keys(sha256s: "set[str]") -> "set[str]":
    with global_session_scope() as db_session:
        referenched_sha256s = {
            sha256
            for sha256, in db_session.query(Message.data_sha256)
            .filter(Message.data_sha256.in_(sha256s))
            .all()
        }

    return sha256s - referenched_sha256s


DELETE_BATCH_SIZE = 100


def do_delete_batch(delete_sha256s: "set[str]", dry_run: bool) -> None:
    if not delete_sha256s:
        return

    if not dry_run:
        blockstore.delete_from_blockstore(*delete_sha256s)
        print("deleted", len(delete_sha256s), "blobs")
    else:
        print("would-delete", len(delete_sha256s), "blobs")


@click.command()
@click.option("--limit", type=int, default=None)
@click.option("--marker", type=str, default=None)
@click.option("--batch-size", type=int, default=10000)
@click.option("--dry-run/--no-dry-run", default=True)
@click.option("--delete-executor-workers", type=int, default=40)
def run(
    limit: "int | None",
    marker: "str | None",
    batch_size: int,
    dry_run: bool,
    delete_executor_workers: int,
) -> None:
    assert limit is None or limit > 0

    shutting_down = False

    def shutdown(signum, frame):
        nonlocal shutting_down
        shutting_down = True

        print("Shutting down...")

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    delete_executor = ThreadPoolExecutor(max_workers=delete_executor_workers)

    get_abandoned_batch = set()
    delete_batch = set()

    for sha256 in find_keys(limit, marker):
        if shutting_down:
            break

        print(sha256)

        get_abandoned_batch.add(sha256)

        if len(get_abandoned_batch) >= batch_size:
            for abandoned_sha256 in get_abandoned_keys(get_abandoned_batch):
                delete_batch.add(abandoned_sha256)

                if len(delete_batch) >= DELETE_BATCH_SIZE:
                    delete_executor.submit(
                        do_delete_batch, delete_batch.copy(), dry_run
                    )
                    delete_batch.clear()

            get_abandoned_batch.clear()

    delete_batch = get_abandoned_keys(get_abandoned_batch)
    delete_executor.submit(do_delete_batch, delete_batch.copy(), dry_run)

    delete_executor.shutdown(wait=True)


if __name__ == "__main__":
    run()

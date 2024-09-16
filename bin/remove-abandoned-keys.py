#!/usr/bin/env python
from gevent import monkey

monkey.patch_all()

from collections.abc import Iterable

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
            yielded += 1

            if limit is not None and yielded >= limit:
                return

        if not all_keys.is_truncated:
            return

        marker = all_keys[-1].name


def get_abandoned_keys(sha256s: "set[str]") -> "str[str]":
    with global_session_scope() as db_session:
        referenched_sha256s = {
            sha256
            for sha256, in db_session.query(Message.data_sha256)
            .filter(Message.data_sha256.in_(sha256s))
            .all()
        }

    return sha256s - referenched_sha256s


@click.command()
@click.option("--limit", type=int, default=None)
@click.option("--marker", type=str, default=None)
@click.option("--batch-size", type=int, default=10000)
@click.option("--dry-run/--no-dry-run", default=True)
def run(
    limit: "int | None", marker: "str | None", batch_size: int, dry_run: bool
) -> None:
    assert limit is None or limit > 0

    get_abandoned_batch = set()

    for offset, sha256 in enumerate(find_keys(limit, marker)):
        print_arguments = [offset]
        if limit is not None:
            print_arguments.append(limit)
        print_arguments.append(sha256)

        print(*print_arguments)

        get_abandoned_batch.add(sha256)

        if len(get_abandoned_batch) >= batch_size:
            for abandoned_sha256 in get_abandoned_keys(get_abandoned_batch):
                print("Abandoned", abandoned_sha256)

            get_abandoned_batch.clear()


if __name__ == "__main__":
    run()

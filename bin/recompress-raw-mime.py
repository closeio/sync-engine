#!/usr/bin/env python
from gevent import monkey

monkey.patch_all()

import datetime
import enum
import gc
import logging
import signal
import time
from collections.abc import Iterable
from concurrent import futures
from concurrent.futures import Future, ThreadPoolExecutor

import click
import zstandard
from sqlalchemy.orm import Query
from sqlalchemy.sql import func

from inbox.logging import configure_logging, get_logger
from inbox.models.message import Message
from inbox.models.session import global_session_scope
from inbox.util import blockstore

configure_logging(logging.ERROR)
log = get_logger()


DEFAULT_RECOMPRESS_BATCH_SIZE = 100
DEFAULT_BATCH_SIZE = 1000
MAX_RECOMPRESS_BATCH_BYTES = 100 * 1024 * 1024  # 100 MB


class Resolution(enum.Enum):
    NOT_PRESENT = "not-present"
    RECOMPRESS = "recompress"
    SKIP = "skip"


# https://stackoverflow.com/questions/73395864/how-do-i-wait-when-all-threadpoolexecutor-threads-are-busy
class AvailableThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor that keeps track of the number of available workers.

    Refs:
        inspired by https://stackoverflow.com/a/73396000/8388869
    """

    def __init__(
        self, max_workers=None, thread_name_prefix="", initializer=None, initargs=()
    ):
        super().__init__(max_workers, thread_name_prefix, initializer, initargs)
        self._running_worker_futures: set[Future] = set()

    @property
    def available_workers(self) -> int:
        """the number of available workers"""
        return self._max_workers - len(self._running_worker_futures)

    def wait_for_available_worker(self, timeout: "float | None" = None) -> None:
        """wait until there is an available worker

        Args:
            timeout: the maximum time to wait in seconds. If None, wait indefinitely.

        Raises:
            TimeoutError: if the timeout is reached.
        """

        start_time = time.monotonic()
        while True:
            if self.available_workers > 0:
                return
            if timeout is not None and time.monotonic() - start_time > timeout:
                raise TimeoutError
            time.sleep(0.1)

    def submit(self, fn, /, *args, **kwargs):
        f = super().submit(fn, *args, **kwargs)
        self._running_worker_futures.add(f)
        f.add_done_callback(self._running_worker_futures.remove)
        return f


def find_messages(
    limit: "int | None",
    after: "datetime.datetime | None",
    before: "datetime.datetime | None",
    after_id: "int | None",
    before_id: "int | None",
    namespace_id: "int | None",
    batch_size: int,
    max_size: "int | None",
) -> "Iterable[tuple[Message, int]]":
    query = Query([Message]).order_by(Message.id)

    if after:
        query = query.filter(Message.created_at >= after)
    if before:
        query = query.filter(Message.created_at < before)
    if after_id:
        query = query.filter(Message.id >= after_id)
    if before_id:
        query = query.filter(Message.id < before_id)
    if namespace_id:
        query = query.filter(Message.namespace_id == namespace_id)
    if max_size:
        query = query.filter(Message.size <= max_size)

    inner_max_id_query = query.with_entities(Message.id)
    if limit is not None:
        inner_max_id_query = inner_max_id_query.limit(limit)

    with global_session_scope() as db_session:
        max_id = db_session.query(func.max(inner_max_id_query.subquery().c.id)).scalar()

    offset = 0
    start_id = 1 if after_id is None else after_id

    while True:
        with global_session_scope() as db_session:
            message_batch = (
                query.filter(Message.id >= start_id)
                .limit(min(limit, batch_size) if limit is not None else batch_size)
                .with_session(db_session)
                .all()
            )

        if not message_batch:
            return

        seen_sha256s = set()
        for message in message_batch:
            if limit is not None and offset >= limit:
                return

            if message.data_sha256 not in seen_sha256s:
                yield message, max_id
                seen_sha256s.add(message.data_sha256)

            offset += 1  # noqa: SIM113

        start_id = message_batch[-1].id + 1


def download_parallel(data_sha256s: "set[str]") -> "Iterable[tuple[str, bytes | None]]":
    with ThreadPoolExecutor(max_workers=DEFAULT_RECOMPRESS_BATCH_SIZE) as executor:
        future_to_sha256 = {
            executor.submit(
                blockstore.get_from_blockstore, data_sha256, check_sha=False
            ): data_sha256
            for data_sha256 in data_sha256s
        }

        for future in futures.as_completed(future_to_sha256):
            data_sha256 = future_to_sha256[future]
            exception = future.exception()

            if not exception:
                yield data_sha256, future.result()
            else:
                print(f"Failed to download {data_sha256}: {exception}")


def overwrite_parallel(compressed_raw_mime_by_sha256: "dict[str, bytes]") -> None:
    with ThreadPoolExecutor(max_workers=DEFAULT_RECOMPRESS_BATCH_SIZE) as executor:
        for data_sha256, compressed_raw_mime in compressed_raw_mime_by_sha256.items():
            executor.submit(
                blockstore.save_to_blockstore,
                data_sha256,
                compressed_raw_mime,
                overwrite=True,
            )


def recompress_batch(
    recompress_sha256s: "set[str]", *, dry_run=True, compression_level: int = 3
) -> None:
    if not recompress_sha256s:
        return

    data_by_sha256 = {
        data_sha256: data
        for data_sha256, data in download_parallel(recompress_sha256s)
        if data is not None and not data.startswith(blockstore.ZSTD_MAGIC_NUMBER_PREFIX)
    }

    if not data_by_sha256:
        return

    compress = zstandard.ZstdCompressor(level=compression_level, threads=-1).compress

    mime_sizes_by_sha256 = {}
    compressed_raw_mime_by_sha256 = {}
    for data_sha256, data in data_by_sha256.items():
        # drop the reference to data to save memory
        data_by_sha256[data_sha256] = None

        decompressed_raw_mime = blockstore.maybe_decompress_raw_mime(data)
        compressed_raw_mime = blockstore.maybe_compress_raw_mime(
            decompressed_raw_mime, compress=compress
        )
        mime_sizes_by_sha256[data_sha256] = (
            len(decompressed_raw_mime),
            len(compressed_raw_mime),
        )
        compressed_raw_mime_by_sha256[data_sha256] = compressed_raw_mime

        # drop the reference to data to save memory
        del decompressed_raw_mime
        del compressed_raw_mime
        del data

    for data_sha256, (
        decompressed_raw_mime_length,
        compressed_raw_mime_length,
    ) in sorted(mime_sizes_by_sha256.items()):
        print(
            f"Recompressed {data_sha256}",
            f"{decompressed_raw_mime_length} -> {compressed_raw_mime_length}",
            f"({decompressed_raw_mime_length / compressed_raw_mime_length:.1f}x)",
        )

    decompressed_sum = sum(
        decompressed_raw_mime_length
        for (decompressed_raw_mime_length, _) in mime_sizes_by_sha256.values()
    )
    compressed_sum = sum(
        compressed_raw_mime_length
        for (_, compressed_raw_mime_length) in mime_sizes_by_sha256.values()
    )
    print(
        "Batch recompressed",
        len(compressed_raw_mime_by_sha256),
        f"{decompressed_sum} -> {compressed_sum}",
        f"({decompressed_sum / compressed_sum:.2f}x)",
    )

    if not dry_run:
        overwrite_parallel(compressed_raw_mime_by_sha256)
        print("Batch overwritten", len(compressed_raw_mime_by_sha256))

    del compressed_raw_mime_by_sha256

    gc.collect()


@click.command()
@click.option("--limit", type=int, default=None)
@click.option("--after", type=str, default=None)
@click.option("--before", type=str, default=None)
@click.option("--after-id", type=int, default=None)
@click.option("--before-id", type=int, default=None)
@click.option("--namespace-id", type=int, default=None)
@click.option("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
@click.option(
    "--recompress-batch-size", type=int, default=DEFAULT_RECOMPRESS_BATCH_SIZE
)
@click.option("--recompress-executor-workers", type=int, default=10)
@click.option("--repeat", type=int, default=1)
@click.option("--dry-run/--no-dry-run", default=True)
@click.option("--check-existence/--no-check-existence", default=False)
@click.option("--compression-level", type=int, default=3)
@click.option("--max-size", type=int, default=None)
@click.option(
    "--max-recompress-batch-bytes", type=int, default=MAX_RECOMPRESS_BATCH_BYTES
)
@click.option("--fraction", type=str, default=None)
def run(
    limit: "int | None",
    after: "str | None",
    before: "str | None",
    after_id: "int | None",
    before_id: "int | None",
    namespace_id: "int | None",
    batch_size: int,
    recompress_batch_size: int,
    recompress_executor_workers: int,
    repeat: int,
    dry_run: bool,
    check_existence: bool,
    compression_level: int,
    max_size: "int | None",
    max_recompress_batch_bytes: int,
    fraction: "str | None",
) -> int:
    shutting_down = False

    def shutdown(signum, frame):
        nonlocal shutting_down
        shutting_down = True

        print("Shutting down...")

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    fraction_tuple = None
    if fraction is not None:
        fraction_tuple = tuple(map(int, fraction.split("/")))
        assert len(fraction_tuple) == 2
        assert fraction_tuple[0] >= 0
        assert fraction_tuple[1] > 1
        assert fraction_tuple[0] < fraction_tuple[1]

    assert batch_size > 0
    assert recompress_batch_size > 0

    recompress_executor = AvailableThreadPoolExecutor(
        max_workers=recompress_executor_workers
    )

    for repetition in range(repeat):
        messages = find_messages(
            limit,
            datetime.datetime.fromisoformat(after) if after else None,
            datetime.datetime.fromisoformat(before) if before else None,
            after_id,
            before_id,
            namespace_id,
            batch_size,
            max_size,
        )

        recompress_sha256s = set()
        recompress_bytes = 0

        max_id = None
        for message, max_id in messages:
            if check_existence:
                data = blockstore.get_from_blockstore(
                    message.data_sha256, check_sha=False
                )
            else:
                data = ...  # assume it exists

            if data is None:
                resolution = Resolution.NOT_PRESENT
            else:
                resolution = Resolution.RECOMPRESS

            if (
                fraction_tuple is not None
                and message.id % fraction_tuple[1] != fraction_tuple[0]
            ):
                resolution = Resolution.SKIP

            print_arguments = [
                f"{message.id}/{max_id}",
                message.created_at.date(),
                resolution.value,
                message.data_sha256,
            ]

            if repeat != 1:
                print_arguments.insert(0, repetition)

            print(*print_arguments)

            if resolution is Resolution.RECOMPRESS:
                recompress_sha256s.add(message.data_sha256)
                recompress_bytes += message.size

            if (
                len(recompress_sha256s) >= recompress_batch_size
                or recompress_bytes > max_recompress_batch_bytes
            ):
                recompress_executor.wait_for_available_worker()
                recompress_executor.submit(
                    recompress_batch,
                    recompress_sha256s.copy(),
                    dry_run=dry_run,
                    compression_level=compression_level,
                )
                recompress_sha256s.clear()
                recompress_bytes = 0

                if shutting_down:
                    break

        recompress_executor.submit(
            recompress_batch,
            recompress_sha256s.copy(),
            dry_run=dry_run,
            compression_level=compression_level,
        )

        if shutting_down:
            break

        if max_id is None:
            return

        after_id = max_id + 1

    recompress_executor.shutdown(wait=True)


if __name__ == "__main__":
    run()

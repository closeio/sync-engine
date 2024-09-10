import os
import time
from hashlib import sha256
from typing import Optional

import zstandard

from inbox.config import config
from inbox.logging import get_logger
from inbox.util.stats import statsd_client

log = get_logger()

# TODO: store AWS credentials in a better way.
STORE_MSG_ON_S3 = config.get("STORE_MESSAGES_ON_S3", None)


from boto.s3.connection import S3Connection
from boto.s3.key import Key

# https://github.com/facebook/zstd/blob/dev/doc/zstd_compression_format.md#zstandard-frames
ZSTD_MAGIC_NUMBER_PREFIX = 0xFD2FB528.to_bytes(4, "little")


def _data_file_directory(h):
    return os.path.join(
        config.get_required("MSG_PARTS_DIRECTORY"), h[0], h[1], h[2], h[3], h[4], h[5]
    )


def _data_file_path(h):
    return os.path.join(_data_file_directory(h), h)


def save_raw_mime(
    data_sha256: str, decompressed_raw_mime: bytes, *, compress: "bool | None" = None
) -> int:
    if compress is None:
        compress = config.get("COMPRESS_RAW_MIME", False)

    if compress:
        assert not decompressed_raw_mime.startswith(ZSTD_MAGIC_NUMBER_PREFIX)

        compressed_raw_mime = zstandard.compress(decompressed_raw_mime)

        assert compressed_raw_mime.startswith(ZSTD_MAGIC_NUMBER_PREFIX)

        if len(compressed_raw_mime) > len(decompressed_raw_mime):
            compressed_raw_mime = decompressed_raw_mime
    else:
        compressed_raw_mime = decompressed_raw_mime

    save_to_blockstore(data_sha256, compressed_raw_mime)

    return len(compressed_raw_mime)


def save_to_blockstore(data_sha256: str, data: bytes) -> None:
    assert data is not None
    assert isinstance(data, bytes)

    if len(data) == 0:
        log.warning("Not saving 0-length data blob")
        return

    if STORE_MSG_ON_S3:
        _save_to_s3(data_sha256, data)
    else:
        directory = _data_file_directory(data_sha256)
        os.makedirs(directory, exist_ok=True)

        with open(_data_file_path(data_sha256), "wb") as f:
            f.write(data)


def _save_to_s3(data_sha256: str, data: bytes) -> None:
    assert (
        "TEMP_MESSAGE_STORE_BUCKET_NAME" in config
    ), "Need temp bucket name to store message data!"

    _save_to_s3_bucket(data_sha256, config["TEMP_MESSAGE_STORE_BUCKET_NAME"], data)


def get_s3_bucket(bucket_name):
    conn = S3Connection(
        config.get("AWS_ACCESS_KEY_ID"),
        config.get("AWS_SECRET_ACCESS_KEY"),
        host=config.get("AWS_S3_HOST", S3Connection.DefaultHost),
        port=config.get("AWS_S3_PORT"),
        is_secure=config.get("AWS_S3_IS_SECURE", True),
    )
    return conn.get_bucket(bucket_name, validate=False)


def _save_to_s3_bucket(data_sha256: str, bucket_name: str, data: bytes) -> None:
    assert "AWS_ACCESS_KEY_ID" in config, "Need AWS key!"
    assert "AWS_SECRET_ACCESS_KEY" in config, "Need AWS secret!"
    start = time.time()

    # Boto pools connections at the class level
    bucket = get_s3_bucket(bucket_name)

    # See if it already exists; if so, don't recreate.
    key = bucket.get_key(data_sha256)
    if key:
        return

    key = Key(bucket)
    key.key = data_sha256
    key.set_contents_from_string(data)

    end = time.time()
    latency_millis = (end - start) * 1000
    statsd_client.timing("s3_blockstore.save_latency", latency_millis)


def get_from_blockstore(data_sha256, *, check_sha=True) -> Optional[bytes]:
    if STORE_MSG_ON_S3:
        value = _get_from_s3(data_sha256)
    else:
        value = _get_from_disk(data_sha256)

    if value is None:
        # The block may have expired.
        log.warning("No data returned!")
        return value

    if check_sha:
        assert (
            data_sha256 == sha256(value).hexdigest()
        ), "Returned data doesn't match stored hash!"

    return value


def get_raw_mime(data_sha256: str) -> "bytes | None":
    compressed_raw_mime = get_from_blockstore(data_sha256, check_sha=False)
    if compressed_raw_mime is None:
        return None

    if compressed_raw_mime.startswith(ZSTD_MAGIC_NUMBER_PREFIX):
        decompressed_raw_mime = zstandard.decompress(compressed_raw_mime)
    else:
        decompressed_raw_mime = compressed_raw_mime

    assert (
        sha256(decompressed_raw_mime).hexdigest() == data_sha256
    ), "Returned data doesn't match stored hash!"

    return decompressed_raw_mime


def _get_from_s3(data_sha256):
    assert "AWS_ACCESS_KEY_ID" in config, "Need AWS key!"
    assert "AWS_SECRET_ACCESS_KEY" in config, "Need AWS secret!"

    assert (
        "TEMP_MESSAGE_STORE_BUCKET_NAME" in config
    ), "Need temp bucket name to store message data!"

    # Try getting data from our temporary blockstore before
    # trying getting it from the provider.
    data = _get_from_s3_bucket(
        data_sha256, config.get("TEMP_MESSAGE_STORE_BUCKET_NAME")
    )

    if data is not None:
        log.info(
            "Found hash in temporary blockstore!",
            sha256=data_sha256,
            logstash_tag="s3_direct",
        )
        return data

    log.info(
        "Couldn't find data in blockstore", sha256=data_sha256, logstash_tag="s3_direct"
    )

    return None


def _get_from_s3_bucket(data_sha256, bucket_name):
    if not data_sha256:
        return None

    bucket = get_s3_bucket(bucket_name)

    key = bucket.get_key(data_sha256)

    if not key:
        log.warning(f"No key with name: {data_sha256} returned!")
        return None

    return key.get_contents_as_string()


def _get_from_disk(data_sha256):
    if not data_sha256:
        return None

    try:
        with open(_data_file_path(data_sha256), "rb") as f:
            return f.read()
    except OSError:
        log.warning(f"No file with name: {data_sha256}!")
        return None


def _delete_from_s3_bucket(data_sha256_hashes, bucket_name):
    data_sha256_hashes = [hash_ for hash_ in data_sha256_hashes if hash_]
    if not data_sha256_hashes:
        return

    assert "AWS_ACCESS_KEY_ID" in config, "Need AWS key!"
    assert "AWS_SECRET_ACCESS_KEY" in config, "Need AWS secret!"
    start = time.time()

    # Boto pools connections at the class level
    bucket = get_s3_bucket(bucket_name)

    bucket.delete_keys([key for key in data_sha256_hashes], quiet=True)

    end = time.time()
    latency_millis = (end - start) * 1000
    statsd_client.timing("s3_blockstore.delete_latency", latency_millis)


def _delete_from_disk(data_sha256):
    if not data_sha256:
        return

    try:
        os.remove(_data_file_path(data_sha256))
    except OSError:
        log.warning(f"No file with name: {data_sha256}!")


def delete_from_blockstore(*data_sha256_hashes):
    log.info("deleting from blockstore", sha256=data_sha256_hashes)

    if STORE_MSG_ON_S3:
        _delete_from_s3_bucket(
            data_sha256_hashes, config.get("TEMP_MESSAGE_STORE_BUCKET_NAME")
        )
    else:
        for data_sha256 in data_sha256_hashes:
            _delete_from_disk(data_sha256)

import io
import os
import time
from collections.abc import Callable
from hashlib import sha256
from typing import Iterable, Optional

import zstandard

from inbox.config import config
from inbox.logging import get_logger
from inbox.util.itert import chunk
from inbox.util.stats import statsd_client

log = get_logger()

# TODO: store AWS credentials in a better way.
STORE_MSG_ON_S3 = config.get("STORE_MESSAGES_ON_S3", None)

import boto3
import botocore.exceptions

# https://github.com/facebook/zstd/blob/dev/doc/zstd_compression_format.md#zstandard-frames
# > This value was selected to be less probable to find at the beginning of some random file.
# > It avoids trivial patterns (0x00, 0xFF, repeated bytes, increasing bytes, etc.),
# > contains byte values outside of ASCII range, and doesn't map into UTF8 space.
# > It reduces the chances that a text file represent this value by accident.
ZSTD_MAGIC_NUMBER_PREFIX = 0xFD2FB528.to_bytes(4, "little")


def _data_file_directory(h):
    return os.path.join(
        config.get_required("MSG_PARTS_DIRECTORY"), h[0], h[1], h[2], h[3], h[4], h[5]
    )


def _data_file_path(h):
    return os.path.join(_data_file_directory(h), h)


def maybe_compress_raw_mime(
    decompressed_raw_mime: bytes,
    *,
    compress: "bool | Callable[[bytes], bytes] | None" = None,
) -> bytes:
    """
    Optionally compress the raw MIME data.

    Args:
        decompressed_raw_mime: The raw MIME data, always *decompressed*.
        compress:
            Whether to compress the data.
            If None, the value of `config["COMPRESS_RAW_MIME"]` is used
            which defaults to False. If True, the data is compressed using
            default compression level which is 3.
            You can also pass in a custom compression function i.e.
            `ZstdCompressor(level=level).compress` if you want to control
            the compression level or other options.


    Returns:
        The optionally compressed raw MIME data.
    """
    if compress is None:
        compress = config.get("COMPRESS_RAW_MIME", False)

    if compress is True:
        compress = zstandard.compress

    assert compress is False or callable(compress)

    if compress:
        # Raw MIME data will never start with the ZSTD magic number,
        # because email messages always start with headers in 7-bit ASCII.
        # ZSTD magic number contains bytes with the highest bit set to 1,
        # so we can use it as a marker to check if the data is compressed.
        assert not decompressed_raw_mime.startswith(ZSTD_MAGIC_NUMBER_PREFIX)

        compressed_raw_mime = compress(decompressed_raw_mime)

        assert compressed_raw_mime.startswith(ZSTD_MAGIC_NUMBER_PREFIX)

        if len(compressed_raw_mime) > len(decompressed_raw_mime):
            # This will not happen in practice, since even the most trivial email
            # these days will have a lot of headers that can be compressed.
            # But if it does, we should always store the smallest possible representation.
            compressed_raw_mime = decompressed_raw_mime
    else:
        compressed_raw_mime = decompressed_raw_mime

    return compressed_raw_mime


def save_raw_mime(
    data_sha256: str, decompressed_raw_mime: bytes, *, compress: "bool | None" = None
) -> int:
    """
    Save the raw MIME data to the blockstore, optionally compressing it.

    Args:
        data_sha256: The SHA256 hash of the *uncompressed* data.
        decompressed_raw_mime: The raw MIME data.
        compress:
            Whether to compress the data before storing it.
            If None, the value of `config["COMPRESS_RAW_MIME"]` is used
            which defaults to False.

    Returns:
        The length of the data in the datastore.
    """
    compressed_raw_mime = maybe_compress_raw_mime(
        decompressed_raw_mime, compress=compress
    )

    save_to_blockstore(data_sha256, compressed_raw_mime)

    return len(compressed_raw_mime)


def save_to_blockstore(
    data_sha256: str, data: bytes, *, overwrite: bool = False
) -> None:
    assert data is not None
    assert isinstance(data, bytes)

    if len(data) == 0:
        log.warning("Not saving 0-length data blob")
        return

    if STORE_MSG_ON_S3:
        _save_to_s3(data_sha256, data, overwrite=overwrite)
    else:
        directory = _data_file_directory(data_sha256)
        os.makedirs(directory, exist_ok=True)

        with open(_data_file_path(data_sha256), "wb") as f:
            f.write(data)


def _save_to_s3(data_sha256: str, data: bytes, *, overwrite: bool = False) -> None:
    assert (
        "TEMP_MESSAGE_STORE_BUCKET_NAME" in config
    ), "Need temp bucket name to store message data!"

    _save_to_s3_bucket(
        data_sha256, config["TEMP_MESSAGE_STORE_BUCKET_NAME"], data, overwrite=overwrite
    )


def get_s3_bucket(bucket_name):
    resource = boto3.resource(
        "s3",
        aws_access_key_id=config.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=config.get("AWS_SECRET_ACCESS_KEY"),
        endpoint_url=config.get("AWS_S3_ENDPOINT_URL"),
    )

    return resource.Bucket(bucket_name)


def _s3_key_exists(bucket, key) -> bool:
    """
    Check if a key exists in an S3 bucket by doing a HEAD request.
    """
    try:
        bucket.Object(key).load()
    except botocore.exceptions.ClientError as e:
        if e.response["Error"]["Code"] == "404":
            return False
        else:
            raise

    return True


def _save_to_s3_bucket(
    data_sha256: str, bucket_name: str, data: bytes, *, overwrite: bool = False
) -> None:
    assert "AWS_ACCESS_KEY_ID" in config, "Need AWS key!"
    assert "AWS_SECRET_ACCESS_KEY" in config, "Need AWS secret!"
    start = time.time()

    # Boto pools connections at the class level
    bucket = get_s3_bucket(bucket_name)

    # See if it already exists; if so, don't recreate.
    if _s3_key_exists(bucket, data_sha256) and not overwrite:
        return

    file_object = io.BytesIO(data)
    bucket.upload_fileobj(file_object, data_sha256)

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


def maybe_decompress_raw_mime(compressed_raw_mime: bytes) -> bytes:
    """
    Decompress the raw MIME data if it's compressed.

    Args:
        compressed_raw_mime: The raw MIME data, either compressed or not.

    Returns:
        The decompressed raw MIME data.
    """
    # Raw MIME data will never start with the ZSTD magic number,
    # because email messages always start with headers in 7-bit ASCII.
    # ZSTD magic number contains bytes with the highest bit set to 1,
    # so we can use it as a marker to check if the data is compressed.
    if compressed_raw_mime.startswith(ZSTD_MAGIC_NUMBER_PREFIX):
        return zstandard.decompress(compressed_raw_mime)
    else:
        return compressed_raw_mime


def get_raw_mime(data_sha256: str) -> "bytes | None":
    """
    Get the raw MIME data from the blockstore.

    The data may be compressed, so this function will decompress it if necessary.

    Args:
        data_sha256: The SHA256 hash of the *uncompressed* data.

    Returns:
        The raw MIME data, or None if it wasn't found.
    """
    compressed_raw_mime = get_from_blockstore(data_sha256, check_sha=False)
    if compressed_raw_mime is None:
        return None

    decompressed_raw_mime = maybe_decompress_raw_mime(compressed_raw_mime)

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


def _get_from_s3_bucket(data_sha256: str, bucket_name: str) -> "bytes | None":
    if not data_sha256:
        return None

    bucket = get_s3_bucket(bucket_name)

    file_object = io.BytesIO()
    try:
        bucket.download_fileobj(data_sha256, file_object)
    except botocore.exceptions.ClientError as e:
        if e.response["Error"]["Code"] == "404":
            log.warning(f"No key with name: {data_sha256} returned!")
            return None
        else:
            raise

    return file_object.getvalue()


def _get_from_disk(data_sha256):
    if not data_sha256:
        return None

    try:
        with open(_data_file_path(data_sha256), "rb") as f:
            return f.read()
    except OSError:
        log.warning(f"No file with name: {data_sha256}!")
        return None


def _delete_from_s3_bucket(
    data_sha256_hashes: "Iterable[str]", bucket_name: str
) -> None:
    data_sha256_hashes = [hash_ for hash_ in data_sha256_hashes if hash_]
    if not data_sha256_hashes:
        return

    assert "AWS_ACCESS_KEY_ID" in config, "Need AWS key!"
    assert "AWS_SECRET_ACCESS_KEY" in config, "Need AWS secret!"
    start = time.time()

    # Boto pools connections at the class level
    bucket = get_s3_bucket(bucket_name)

    # As per https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/delete_objects.html
    # we can only delete 1000 objects at a time.
    for data_sha256_hashes_chunk in chunk(data_sha256_hashes, 1000):
        bucket.delete_objects(
            Delete={
                "Objects": [{"Key": key} for key in data_sha256_hashes_chunk],
                "Quiet": True,
            }
        )

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

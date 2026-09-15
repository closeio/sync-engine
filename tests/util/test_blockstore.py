import hashlib
import pathlib
from unittest import mock

import pytest

from inbox.util import blockstore


def test_retry_on_s3_endpoint_connection_error() -> None:
    call_count = 0

    @blockstore.retry_on_s3_endpoint_connection_error
    def eventually_succeeds() -> None:
        nonlocal call_count
        call_count += 1
        if call_count <= len(blockstore.S3_RETRY_DELAYS):
            raise blockstore.botocore.exceptions.EndpointConnectionError(
                endpoint_url="https://example.com"
            )

    with mock.patch.object(blockstore.interruptible_threading, "sleep"):
        eventually_succeeds()


def test_retry_on_s3_endpoint_connection_error_gives_up() -> None:
    always_fails = blockstore.retry_on_s3_endpoint_connection_error(
        mock.Mock(
            side_effect=blockstore.botocore.exceptions.EndpointConnectionError(
                endpoint_url="https://example.com"
            )
        )
    )

    with (
        mock.patch.object(blockstore.interruptible_threading, "sleep"),
        pytest.raises(blockstore.botocore.exceptions.EndpointConnectionError),
    ):
        always_fails()


@pytest.mark.usefixtures("blockstore_backend")
@pytest.mark.parametrize("blockstore_backend", ["disk", "s3"], indirect=True)
def test_save_to_and_get_from_blockstore() -> None:
    data = b"test data"
    data_sha256 = hashlib.sha256(data).hexdigest()
    blockstore.save_to_blockstore(data_sha256, data)
    assert blockstore.get_from_blockstore(data_sha256) == data


@pytest.fixture
def tiny_email_data() -> bytes:
    return (pathlib.Path(__file__).parent / "tiny.eml").read_bytes()


@pytest.mark.usefixtures("blockstore_backend")
@pytest.mark.parametrize("blockstore_backend", ["disk", "s3"], indirect=True)
def test_save_and_get_raw_mime_no_compression(tiny_email_data) -> None:
    data_sha256 = hashlib.sha256(tiny_email_data).hexdigest()
    stored_length = blockstore.save_raw_mime(
        data_sha256, tiny_email_data, compress=False
    )

    assert stored_length == len(tiny_email_data)
    assert blockstore.get_raw_mime(data_sha256) == tiny_email_data


@pytest.mark.usefixtures("blockstore_backend")
@pytest.mark.parametrize("blockstore_backend", ["disk", "s3"], indirect=True)
def test_save_and_get_raw_mime_with_compression(tiny_email_data) -> None:
    data_sha256 = hashlib.sha256(tiny_email_data).hexdigest()
    stored_length = blockstore.save_raw_mime(
        data_sha256, tiny_email_data, compress=True
    )

    assert stored_length < len(tiny_email_data)
    assert blockstore.get_raw_mime(data_sha256) == tiny_email_data

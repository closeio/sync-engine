import hashlib
import pathlib

import pytest

from inbox.util import blockstore


@pytest.mark.usefixtures("blockstore_backend")
@pytest.mark.parametrize("blockstore_backend", ["disk", "s3"], indirect=True)
def test_save_to_and_get_from_blockstore():
    data = b"test data"
    data_sha256 = hashlib.sha256(data).hexdigest()
    blockstore.save_to_blockstore(data_sha256, data)
    assert blockstore.get_from_blockstore(data_sha256) == data


@pytest.fixture
def tiny_email_data() -> bytes:
    return (pathlib.Path(__file__).parent / "tiny.eml").read_bytes()


@pytest.mark.usefixtures("blockstore_backend")
@pytest.mark.parametrize("blockstore_backend", ["disk", "s3"], indirect=True)
def test_save_and_get_raw_mime_no_compression(tiny_email_data):
    data_sha256 = hashlib.sha256(tiny_email_data).hexdigest()
    stored_length = blockstore.save_raw_mime(
        data_sha256, tiny_email_data, compress=False
    )

    assert stored_length == len(tiny_email_data)
    assert blockstore.get_raw_mime(data_sha256) == tiny_email_data


@pytest.mark.usefixtures("blockstore_backend")
@pytest.mark.parametrize("blockstore_backend", ["disk", "s3"], indirect=True)
def test_save_and_get_raw_mime_with_compression(tiny_email_data):
    data_sha256 = hashlib.sha256(tiny_email_data).hexdigest()
    stored_length = blockstore.save_raw_mime(
        data_sha256, tiny_email_data, compress=True
    )

    assert stored_length < len(tiny_email_data)
    assert blockstore.get_raw_mime(data_sha256) == tiny_email_data

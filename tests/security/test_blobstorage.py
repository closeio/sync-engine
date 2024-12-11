import zlib

import hypothesis
from hypothesis import strategies as s

from inbox.models.message import Message
from inbox.security.blobstorage import decode_blob, encode_blob


# This will run the test for a bunch of randomly-chosen values of sample_input.
@hypothesis.given(s.binary(), s.booleans())
def test_blobstorage(config, sample_input, encrypt):
    config["ENCRYPT_SECRETS"] = encrypt
    assert decode_blob(encode_blob(sample_input)) == sample_input


@hypothesis.given(s.binary(), s.booleans())
def test_encoded_format(config, sample_input, encrypt):
    config["ENCRYPT_SECRETS"] = encrypt
    encoded = encode_blob(sample_input)
    assert encoded.startswith(
        (b"\x01" if encrypt else b"\x00") + b"\x00\x00\x00\x00"
    )
    data = encoded[5:]
    if encrypt:
        assert data != sample_input
        assert data != zlib.compress(sample_input)
    else:
        assert data == zlib.compress(sample_input)


@hypothesis.given(s.text(), s.booleans())
def test_message_body_storage(config, sample_input, encrypt):
    message = Message()
    config["ENCRYPT_SECRETS"] = encrypt
    message.body = None
    assert message._compacted_body is None
    message.body = sample_input
    assert message._compacted_body.startswith(
        (b"\x01" if encrypt else b"\x00") + b"\x00\x00\x00\x00"
    )
    assert message.body == sample_input

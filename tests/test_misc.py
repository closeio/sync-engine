import pytest

from inbox.models.backends.oauth import hash_token


@pytest.mark.parametrize(
    "prefix,token,expected",
    [
        (None, None, None),
        ("prefix", None, None),
        (
            None,
            "secret_token",
            "26d141c9a7204906121db95e892d2c8b500c3db440c06d3eaa6715f0cbf8763f",
        ),
        (
            "prefix",
            "secret_token",
            "c848fa75ec9d92cf38b68f5fd3db6ae1941d3bebebdb5c353878a656aa4dfa4a",
        ),
    ],
)
def test_hash_token(token, prefix, expected):
    result = hash_token(token, prefix)
    assert result == expected

import pytest

from inbox.uid_set import (
    UidSet,
    compress_ranges,
    decode_compressed_ranges,
    decompress_ranges,
    encode_compressed_ranges,
)


@pytest.mark.parametrize(
    "example,expected",
    (
        ([], []),
        ([1], [1]),
        ([1, 2], [(1, 2)]),
        ([1, 2, 3], [(1, 3)]),
        ([3, 1, 2], [(1, 3)]),
        ([1, 2, 4], [(1, 2), 4]),
        ([1, 3, 4], [1, (3, 4)]),
        ([1, 2, 4, 5], [(1, 2), (4, 5)]),
        ([1, 2, 3, 5, 7, 8, 9], [(1, 3), 5, (7, 9)]),
        ([1, 3, 4, 5, 7, 8, 9], [1, (3, 5), (7, 9)]),
    ),
)
def test_compress_decompress_ranges(example, expected):
    assert list(compress_ranges(example)) == expected
    assert list(decompress_ranges(expected)) == sorted(example)


@pytest.mark.parametrize(
    "compressed_ranges,encoded_ranges",
    (
        ([], []),
        ([1], [b"\x01\0\0\0"]),
        ([(1, 9)], [b"\0\0\0\0\x01\0\0\0\x09\0\0\0"]),
        (
            [1, (3, 9), 7],
            [b"\x01\0\0\0", b"\0\0\0\0\x03\0\0\0\x09\0\0\0", b"\x07\0\0\0"],
        ),
    ),
)
def test_encode_compressed_ranges(compressed_ranges, encoded_ranges):
    assert list(encode_compressed_ranges(compressed_ranges)) == encoded_ranges
    assert list(decode_compressed_ranges(encoded_ranges)) == compressed_ranges


@pytest.mark.parametrize(
    "iterable",
    (
        [],
        [1],
        [1, 2],
        [1, 2, 3],
        [3, 1, 2],
        [1, 2, 4],
        [1, 3, 4],
        [1, 2, 4, 5],
        [1, 2, 3, 5, 7, 8, 9],
        [1, 3, 4, 5, 7, 8, 9],
    ),
)
def test_uid_set(iterable):
    assert set(UidSet(iterable)) == set(iterable)

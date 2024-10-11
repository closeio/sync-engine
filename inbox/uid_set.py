import random
from collections.abc import Iterable

MAX_UINT32 = 2**32 - 1


def compress_ranges(iterable: "Iterable[int]") -> "Iterable[int | tuple[int, int]]":
    sorted_iterable = sorted(iterable)

    if not sorted_iterable:
        return

    cache: "int | tuple[int, int] | None" = None

    for element in sorted_iterable:
        assert 0 < element <= MAX_UINT32

        if cache is None:
            cache = element
            continue

        if isinstance(cache, int):
            if element == cache:
                continue

            if element - cache == 1:
                cache = (cache, element)
                continue

        if isinstance(cache, tuple):
            if element == cache[1]:
                continue

            if element - cache[1] == 1:
                cache = (cache[0], element)
                continue

        yield cache
        cache = element

    if cache is not None:
        yield cache


def decompress_ranges(
    compressed_ranges: "Iterable[int | tuple[int, int]]",
) -> "Iterable[int]":
    for element in compressed_ranges:
        if isinstance(element, int):
            yield element
        else:
            start, end = element
            yield from range(start, end + 1)


def encode_compressed_ranges(
    compressed_ranges: "Iterable[int | tuple[int, int]]", *, min_range_distance=4
) -> Iterable[bytes]:
    for element in compressed_ranges:
        if isinstance(element, int):
            yield element.to_bytes(4, "little")
        else:
            start, end = element
            if end - start >= min_range_distance:
                yield b"\0\0\0\0" + start.to_bytes(4, "little") + end.to_bytes(
                    4, "little"
                )
            else:
                yield b"".join(i.to_bytes(4, "little") for i in range(start, end + 1))


def decode_compressed_ranges(
    encoded_ranges: "Iterable[bytes]",
) -> "Iterable[int | tuple[int, int]]":
    for element in encoded_ranges:
        if len(element) == 4:
            yield int.from_bytes(element, "little")
        else:
            start = int.from_bytes(element[4:8], "little")
            end = int.from_bytes(element[8:12], "little")
            yield (start, end)


def tokenize(stream: bytes) -> "Iterable[bytes]":
    # todo memory views
    offset = 0
    while offset < len(stream):
        if stream[offset : offset + 4] == b"\0\0\0\0":
            yield stream[offset : offset + 12]
            offset += 12
        else:
            yield stream[offset : offset + 4]
            offset += 4


class UidSet:
    def __init__(self, iterable: "Iterable[int]"):
        self._data = b"".join(encode_compressed_ranges(compress_ranges(iterable)))

    def __iter__(self) -> Iterable[int]:
        return decompress_ranges(decode_compressed_ranges(tokenize(self._data)))


def make_data(length: int, ratio: float) -> list[int]:
    return [i for i in range(1, int(length * 1 / ratio) + 1) if random.random() < ratio]


def main():
    from pympler.asizeof import asizeof

    for length in (10, 100, 1000, 10000, 100000):
        ten_list = make_data(length, 0.5)
        print("Length: ", len(ten_list))
        print("List size:", asizeof(ten_list))
        ten_uid_set = UidSet(ten_list)
        print("Uid set length:", asizeof(ten_uid_set))
        print(f"Proportion: {asizeof(ten_uid_set) / asizeof(ten_list):.2f}")
        print("=========================")


if __name__ == "__main__":
    main()

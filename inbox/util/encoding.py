from typing import Union


def base36encode(number):
    # type: (int) -> str
    if not isinstance(number, int):
        raise TypeError("number must be an integer")
    if number < 0:
        raise ValueError("number must be positive")

    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

    base36 = ""
    while number:
        number, i = divmod(number, 36)
        base36 = alphabet[i] + base36

    return base36 or alphabet[0]


def base36decode(number):
    # type: (str) -> int
    return int(number, 36)


def unicode_safe_truncate(s, max_length):
    # type: (Union[bytes, str, int], int) -> str
    """
    Implements unicode-safe truncation and trims whitespace for a given input
    string, number or unicode string.
    """
    if isinstance(s, bytes):
        s = s.decode("utf-8", "ignore")
    else:
        s = str(s)

    return s.rstrip()[:max_length]

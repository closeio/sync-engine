import builtins
import sys
from typing import Union

from past.builtins import long


def base36encode(number):
    # type: (int) -> str
    if not isinstance(number, (int, long)):
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
        s = (builtins.unicode if sys.version_info < (3,) else str)(s)

    return s.rstrip()[:max_length]

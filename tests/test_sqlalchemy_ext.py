# -*- coding: utf-8 -*-

import sys

import pytest

import inbox.sqlalchemy_ext.util


@pytest.mark.skipif(
    sys.version_info < (3,), reason="Python 2 behaves like surrogatepass"
)
def test_utf8_surrogate_fix_codec():
    assert "abc".encode("utf8-surrogate-fix") == b"abc"
    assert b"abc".decode("utf8-surrogate-fix") == "abc"

    # 🙏 as single character
    "🙏".encode("utf8-surrogate-fix") == b"\xf0\x9f\x99\x8f"
    b"\xf0\x9f\x99\x8f".decode("utf8-surrogate-fix") == "🙏"

    # 🙏 as two surrogate characters
    with pytest.raises(UnicodeEncodeError):
        ("\ud83d" + "\ude4f").encode("utf8-surrogate-fix")
    (b"\xed\xa0\xbd" + b"\xed\xb9\x8f").decode("utf8-surrogate-fix") == "🙏"

    # first surrogate of 🙏
    with pytest.raises(UnicodeEncodeError):
        "\ud83d".encode("utf8-surrogate-fix")
    assert b"\xed\xa0\xbd".decode("utf8-surrogate-fix") == ""

    # second surrogate of 🙏
    with pytest.raises(UnicodeEncodeError):
        "\ude4f".encode("utf8-surrogate-fix")
    assert b"\xed\xb9\x8f".decode("utf8-surrogate-fix") == ""

    # 🙏 as two surrogate characters and first surrogate of 🙏
    with pytest.raises(UnicodeEncodeError):
        ("\ud83d" + "\ude4f" + "\ud83d").encode("utf8-surrogate-fix")
    (b"\xed\xa0\xbd" + b"\xed\xb9\x8f" + b"\xed\xa0\xbd").decode(
        "utf8-surrogate-fix"
    ) == "🙏"

    # second surrogate of 🙏 and 🙏 as two surrogate characters
    with pytest.raises(UnicodeEncodeError):
        ("\ude4f" + "\ud83d" + "\ude4f").encode("utf8-surrogate-fix")
    assert (b"\xed\xb9\x8f" + b"\xed\xa0\xbd" + b"\xed\xb9\x8f").decode(
        "utf8-surrogate-fix"
    ) == "🙏"

    # 🙏 as single character and 🙏 as two surrogate characters
    with pytest.raises(UnicodeEncodeError):
        ("🙏" + "\ud83d" + "\ude4f").encode("utf8-surrogate-fix")
    (b"\xf0\x9f\x99\x8f" + b"\xed\xa0\xbd" + b"\xed\xb9\x8f").decode(
        "utf8-surrogate-fix"
    ) == "🙏🙏"

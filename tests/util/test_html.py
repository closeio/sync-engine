import pytest

from inbox.util.html import plaintext2html


@pytest.mark.parametrize(
    ("plaintext", "html"),
    [
        ("", "<p></p>"),
        ("a\nb", "<p>a<br>b</p>"),
        ("a\n\nb", "<p>a</p>\n<p>b</p>"),
        ("<", "<p>&lt;</p>"),
        (">", "<p>&gt;</p>"),
        ("R&D", "<p>R&amp;D</p>"),
        (
            "Go to https://example.com",
            '<p>Go to <a href="https://example.com">https://example.com</a></p>',
        ),
    ],
)
def test_plaintext2html(plaintext, html) -> None:
    assert plaintext2html(plaintext) == html

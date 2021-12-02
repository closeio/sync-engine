# -*- coding: utf-8 -*-
import re
import sys

if sys.version_info < (3, 8):
    from cgi import escape as html_escape
else:
    from html import escape as html_escape


if sys.version_info >= (3,):
    unichr = chr
    from html.entities import name2codepoint
    from html.parser import HTMLParser

    class HTMLParseError(Exception):
        pass


else:  # TODO remove this when Python 3 only
    from htmlentitydefs import name2codepoint
    from HTMLParser import HTMLParser, HTMLParseError


from inbox.logging import get_logger


# http://stackoverflow.com/questions/753052/strip-html-from-strings-in-python
class HTMLTagStripper(HTMLParser):
    strippedTags = ["title", "script", "style"]

    def __init__(self):
        self.reset()
        self.fed = []
        self.strip_tag_contents_mode = False

        HTMLParser.__init__(self)

    def handle_starttag(self, tag, attrs):
        # Replace <br>, <div> tags by spaces
        if tag.lower() in ("br", "div"):
            self.fed.append(" ")
        # Strip the contents of a tag when it's
        # in strippedTags. We can do this because
        # HTMLParser won't try to parse the inner
        # contents of a tag.
        if tag.lower() in HTMLTagStripper.strippedTags:
            self.strip_tag_contents_mode = True

    def handle_endtag(self, tag):
        self.strip_tag_contents_mode = False

    def handle_data(self, d):
        if not self.strip_tag_contents_mode:
            self.fed.append(d)

    # TODO: Remove this in Python 3 only
    if sys.version_info < (3,):

        def handle_charref(self, d):
            try:
                if d.startswith("x"):
                    val = int(d[1:], 16)
                else:
                    val = int(d)
                self.fed.append(unichr(val))
            except (ValueError, OverflowError):
                return

    if (3,) <= sys.version_info < (3, 10):

        def error(self, message):
            raise HTMLParseError(message)

    def handle_entityref(self, d):
        try:
            val = unichr(name2codepoint[d])
        except KeyError:
            return
        self.fed.append(val)

    def get_data(self):
        # type: () -> str
        return u"".join(self.fed)


def strip_tags(html):
    # type: (str) -> str
    """
    Return textual content of HTML.
    Remove title, script and style alltogether. Replace br and div
    with space. Expand HTML entities.

    This function can potentially raise HTMLParseError if fed invalid html.
    You are responsible for handling it in the calling function.
    """
    s = HTMLTagStripper()
    s.feed(html)
    return s.get_data()


# https://djangosnippets.org/snippets/19/
re_string = re.compile(
    r"(?P<htmlchars>[<&>])|(?P<space>^[ \t]+)|(?P<lineend>\n)|(?P<protocol>(^|\s)((https?|ftp)://.*?))(\s|$)",
    re.S | re.M | re.I | re.U,
)


def plaintext2html(text, tabstop=4):
    # type: (str, int) -> str
    assert "\r" not in text, "newlines not normalized"

    def do_sub(m):
        c = m.groupdict()
        if c["htmlchars"]:
            return html_escape(c["htmlchars"], False)
        if c["lineend"]:
            return "<br>"
        elif c["space"]:
            t = m.group().replace("\t", u"&nbsp;" * tabstop)
            t = t.replace(" ", "&nbsp;")
            return t
        elif c["space"] == "\t":
            return " " * tabstop
        else:
            url = m.group("protocol")
            if url.startswith(" "):
                prefix = " "
                url = url[1:]
            else:
                prefix = ""
            last = m.groups()[-1]
            if last in ["\n", "\r", "\r\n"]:
                last = "<br>"
            return u'{0}<a href="{1}">{2}</a>{3}'.format(prefix, url, url, last)

    return "\n".join(
        [u"<p>{0}</p>".format(re.sub(re_string, do_sub, p)) for p in text.split("\n\n")]
    )

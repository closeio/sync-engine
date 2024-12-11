"""This module defines strategies for generating test data for IMAP sync, all
well as a mock IMAPClient isntance that can be used to deterministically test
aspects of IMAP sync.
See https://hypothesis.readthedocs.org/en/latest/data.html for more information
about how this works.
"""

import string

import flanker
from flanker import mime
from hypothesis import strategies as s

MAX_INT_VALUE = (1 << 32) - 1


def _build_address_header(addresslist):
    return ", ".join(
        flanker.addresslib.address.EmailAddress(phrase, spec).full_spec()
        for phrase, spec in addresslist
    )


def build_mime_message(from_, to, cc, bcc, subject, body):
    msg = mime.create.multipart("alternative")
    msg.append(mime.create.text("plain", body))
    msg.headers["Subject"] = subject
    msg.headers["From"] = _build_address_header(from_)
    msg.headers["To"] = _build_address_header(to)
    msg.headers["Cc"] = _build_address_header(cc)
    msg.headers["Bcc"] = _build_address_header(bcc)
    return msg.to_string().encode()


def build_uid_data(internaldate, flags, body, g_labels, g_msgid, modseq):
    return {
        b"INTERNALDATE": internaldate,
        b"FLAGS": flags,
        b"BODY[]": body,
        b"RFC822.SIZE": len(body),
        b"X-GM-LABELS": g_labels,
        b"X-GM-MSGID": g_msgid,
        b"X-GM-THRID": g_msgid,  # For simplicity
        b"MODSEQ": (modseq,),
    }


# We don't want to worry about whacky encodings or pathologically long data
# here, so just generate some basic, sane ASCII text.
basic_text = s.text(string.ascii_letters, min_size=1, max_size=64)
short_text = s.text(string.ascii_letters, min_size=1, max_size=32)


# An email address of the form 'foo@bar'.
address = s.builds(
    lambda localpart, domain: f"{localpart}@{domain}", short_text, short_text
)


# A list of tuples ('displayname', 'addr@domain')
addresslist = s.lists(s.tuples(basic_text, address), min_size=1, max_size=5)


# A basic MIME message with plaintext body plus From/To/Cc/Bcc/Subject headers
mime_message = s.builds(
    build_mime_message,
    addresslist,
    addresslist,
    addresslist,
    addresslist,
    basic_text,
    basic_text,
)

randint = s.integers(min_value=0, max_value=MAX_INT_VALUE)

uid_data = s.builds(
    build_uid_data,
    s.datetimes(),
    s.sampled_from([(), (b"\\Seen",)]),
    mime_message,
    s.sampled_from([(), (b"\\Inbox",)]),
    randint,
    randint,
)


uids = s.dictionaries(
    s.integers(min_value=22, max_value=MAX_INT_VALUE),
    uid_data,
    min_size=5,
    max_size=10,
)

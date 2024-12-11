import datetime

import pytest

from inbox.util.misc import get_internaldate

received_header_value = """
    from a39-221.smtp-out.amazonses.com (a39-221.smtp-out.amazonses.com. [54.240.39.221])
    by mx.google.com with ESMTPS id z21-20020ac875d5000000b00343288592dcsi12759qtq.455.2022.09.29.12.07.35
    for <test@example.com>
    (version=TLS1_2 cipher=ECDHE-ECDSA-AES128-GCM-SHA256 bits=128/128);
    Thu, 29 Sep 2022 12:07:35 -0700 (PDT)
"""
received_date = datetime.datetime(2022, 9, 29, 19, 7, 35)

date_header_value = "Thu, 29 Sep 2022 19:07:34 +0000"
date_date = datetime.datetime(2022, 9, 29, 19, 7, 34)


@pytest.mark.parametrize(
    ("date", "received", "expected"),
    [
        (None, received_header_value, received_date),
        (date_header_value, received_header_value, date_date),
        (date_header_value, None, date_date),
    ],
)
def test_get_internaldate(date, received, expected) -> None:
    assert get_internaldate(date, received) == expected

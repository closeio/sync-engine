from datetime import datetime

from inbox.models.event import Event, RecurringEventOverride


def test_base36_validation():
    from inbox.events.util import valid_base36

    assert valid_base36("1234zerzerzedsfsd") is True
    assert valid_base36("zerzerzedsfsd") is True
    assert valid_base36("é(§è!è§('") is False
    assert valid_base36("_°987643") is False


def test_event_organizer_parsing():
    from inbox.models.event import Event

    e = Event.create()
    e.owner = "Jean Lecanuet <jean.lecanuet@orange.fr>"
    assert e.organizer_email == "jean.lecanuet@orange.fr"

    e.owner = u"Pierre Mendès-France <pierre-mendes.france@orange.fr >"
    assert e.organizer_email == "pierre-mendes.france@orange.fr"

    e.owner = u"Pierre Messmer <   pierre.messmer@orange.fr >"
    assert e.organizer_email == "pierre.messmer@orange.fr"


def test_removed_participants():
    from inbox.events.util import removed_participants

    helena = {"email": "helena@nylas.com", "name": "Helena Handbasket"}
    ben = {"email": "ben@nylas.com", "name": "Ben Handbasket"}
    paul = {"email": "paul@nylas.com", "name": "Paul Hochon"}
    helena_case_change = {"email": "HELENA@nylas.com", "name": "Helena Handbasket"}

    assert removed_participants([], []) == []
    assert removed_participants([helena], [ben]) == [helena]
    assert removed_participants([helena, ben], [helena]) == [ben]
    assert removed_participants([helena, ben], [paul, helena]) == [ben]
    assert len(removed_participants([helena, ben, paul], [helena])) == 2
    assert ben in removed_participants([helena, ben, paul], [helena])
    assert paul in removed_participants([helena, ben, paul], [helena])
    assert removed_participants([helena, ben], [helena_case_change, ben]) == []
    removed = removed_participants([helena, ben], [helena_case_change, paul])
    assert ben in removed and len(removed) == 1


def test_unicode_event_truncation(db, default_account):
    emoji_str = u"".join([u"😁" for i in range(300)])
    title = "".join(["a" for i in range(2000)])

    e = Event.create(
        raw_data="",
        busy=True,
        all_day=False,
        read_only=False,
        uid="x" * 1000,
        start=datetime(2015, 2, 22, 11, 11),
        end=datetime(2015, 2, 22, 22, 22),
        is_owner=True,
        calendar=default_account.emailed_events_calendar,
        title=title,
        location=emoji_str,
        participants=[],
    )
    e.namespace = default_account.namespace
    db.session.add(e)
    db.session.commit()

    # Both location and title should be properly truncated to their max lengths.
    # It's ok to have N unicode characters in a VARCHAR(N) field because
    # the column is uft8-encoded.
    assert len(e.location) == 255
    assert len(e.title) == 1024
    assert len(e.uid) == 767

    e = Event.create(
        raw_data="",
        busy=True,
        all_day=False,
        read_only=False,
        uid="y" * 1000,
        start=datetime(2015, 2, 22, 11, 11),
        end=datetime(2015, 2, 22, 22, 22),
        is_owner=True,
        master_event_uid="z" * 1000,
        calendar=default_account.emailed_events_calendar,
        title=title,
        location=emoji_str,
        participants=[],
    )
    e.namespace = default_account.namespace
    db.session.add(e)
    db.session.commit()
    assert len(e.location) == 255
    assert len(e.title) == 1024
    assert len(e.uid) == 767
    assert len(e.master_event_uid) == 767


def test_event_emails():
    from inbox.models.event import Event

    e = Event.create()

    e.description = "Email: test@example.com."
    assert e.emails_from_description == ["test@example.com"]

    e.description = '<a href="mailto:test@example.com">other@example.com</a>'
    assert e.emails_from_description == ["test@example.com", "other@example.com"]

    e.title = "Email: title@example.com"
    assert e.emails_from_title == ["title@example.com"]

    # We're not currently able to extract HTML-encoded email addresses from an
    # HTML event.
    # e.description = '<p>Email: t&#101;st@example.com</p>'
    # assert e.emails_from_description == ['test@example.com']

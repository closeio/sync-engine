def test_invite_generation(event, default_account):
    from inbox.events.ical import generate_icalendar_invite

    event.sequence_number = 1
    event.participants = [
        {"email": "helena@nylas.com"},
        {"email": "myles@nylas.com"},
    ]
    cal = generate_icalendar_invite(event)
    assert cal["method"] == "REQUEST"

    for component in cal.walk():
        if component.name == "VEVENT":
            assert component.get("summary") == event.title
            assert int(component.get("sequence")) == event.sequence_number
            assert component.get("location") == event.location

            attendees = component.get("attendee", [])

            # the iCalendar python module doesn't return a list when
            # there's only one attendee. Go figure.
            if not isinstance(attendees, list):
                attendees = [attendees]

            for attendee in attendees:
                email = str(attendee)
                # strip mailto: if it exists
                if email.lower().startswith("mailto:"):
                    email = email[7:]

                assert email in ["helena@nylas.com", "myles@nylas.com"]


def test_message_generation(event, default_account):
    from inbox.events.ical import generate_invite_message

    event.title = "A long walk on the beach"
    event.participants = [{"email": "helena@nylas.com"}]
    msg = generate_invite_message("empty", event, default_account)

    # Make sure the From header is set correctly
    assert msg.headers["From"] == "automated@notifications.nylas.com"

    # Check that we have an email with an HTML part, a plain text part, a
    # text/calendar with METHOD=REQUEST and an attachment.
    count = 0
    for mimepart in msg.walk(with_self=msg.content_type.is_singlepart()):
        format_type = mimepart.content_type.format_type
        subtype = mimepart.content_type.subtype

        if (format_type, subtype) in [
            ("text", "plain"),
            ("text", "html"),
            ("text", "calendar; method=request"),
            ("application", "ics"),
        ]:
            count += 1
    assert count == 3


def test_unicode_message_generation(event, default_account):
    from inbox.events.ical import generate_invite_message

    event.title = "Dîner chez François et Hélène"
    event.description = """Cher Paul,
Je suis heureux de vous inviter à un diner le samedi 19 novembre 2011 à 19h30
au chalet de l'île Daumesnil dans le bois de Vincennes.

Je vous attend avec impatience. Bien à vous.
Hélène (Ἑλένη)
"""

    event.participants = [{"email": "hélène@nylas.com"}]
    generate_invite_message("empty", event, default_account)

    # That's it --- we just needed to make sure message
    # generation shouldn't blow up.

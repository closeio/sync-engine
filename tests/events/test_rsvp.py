from inbox.events.ical import rsvp_recipient
from inbox.models.event import Event


def test_rsvp_recipient(default_account, message) -> None:
    assert rsvp_recipient(None) is None

    event = Event.create()
    event.owner = "Georges Perec <georges@gmail.com>"
    assert rsvp_recipient(event) == "georges@gmail.com"

    event = Event.create()
    event.owner = "<perec@gmail.com>"
    assert rsvp_recipient(event) == "perec@gmail.com"

    event = Event.create()
    event.owner = "perec@gmail.com"
    assert rsvp_recipient(event) == "perec@gmail.com"

    event.owner = "None <None>"
    assert rsvp_recipient(event) is None

    message.from_addr = [("Georges Perec", "georges@gmail.com")]
    event = Event.create()
    event.owner = None
    event.message = message
    assert rsvp_recipient(event) == message.from_addr[0][1]

    message.from_addr = None
    assert rsvp_recipient(event) is None

    message.from_addr = []
    assert rsvp_recipient(event) is None

    message.from_addr = [("", "")]
    assert rsvp_recipient(event) is None

    message.from_addr = [("Georges Sans Addresse", "")]
    assert rsvp_recipient(event) is None

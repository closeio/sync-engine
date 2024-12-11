from inbox.scheduling.event_queue import EventQueue, EventQueueGroup


def test_event_queue() -> None:
    queue = EventQueue("name")
    sent_event = {"event": "test"}
    queue.send_event(sent_event)
    received_event = queue.receive_event()

    assert received_event.pop("queue_name") == queue.queue_name
    assert received_event == sent_event


def test_event_queue_timeout_none() -> None:
    queue = EventQueue("name")
    sent_event = {"event": "test"}
    queue.send_event(sent_event)
    received_event = queue.receive_event(None)

    assert received_event.pop("queue_name") == queue.queue_name
    assert received_event == sent_event


def test_event_queue_empty() -> None:
    queue = EventQueue("name")
    assert queue.receive_event(1) is None
    assert queue.receive_event(None) is None


def test_event_queue_group() -> None:
    queue1 = EventQueue("name1")
    queue2 = EventQueue("name2")

    sent_event_1 = {"event": "test1"}
    queue1.send_event(sent_event_1)

    sent_event_2 = {"event": "test2"}
    queue2.send_event(sent_event_2)

    queue_group = EventQueueGroup([queue1, queue2])

    received_event_1 = queue_group.receive_event()
    assert received_event_1.pop("queue_name") == queue1.queue_name
    assert received_event_1 == sent_event_1

    received_event_2 = queue_group.receive_event()
    assert received_event_2.pop("queue_name") == queue2.queue_name
    assert received_event_2 == sent_event_2

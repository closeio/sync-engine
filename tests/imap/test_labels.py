import json

import pytest

from inbox.mailsync.backends.imap.common import update_message_metadata
from inbox.models.backends.imap import LabelItem
from inbox.models.constants import MAX_INDEXABLE_LENGTH
from tests.util.base import (
    add_fake_folder,
    add_fake_imapuid,
    add_fake_message,
    add_fake_thread,
    default_account,
)

__all__ = ["default_account"]


def add_fake_label(db_session, default_account, display_name, name):
    from inbox.models.label import Label

    return Label.find_or_create(
        db_session, default_account, display_name, name
    )


@pytest.fixture
def folder_and_message_maps(db, default_account):
    folder_map, message_map = {}, {}
    for name in ("all", "trash", "spam"):
        # Create a folder
        display_name = name.capitalize() if name != "all" else "All Mail"
        folder = add_fake_folder(
            db.session, default_account, display_name, name
        )
        thread = add_fake_thread(db.session, default_account.namespace.id)
        # Create a message in the folder
        message = add_fake_message(
            db.session, default_account.namespace.id, thread
        )
        add_fake_imapuid(db.session, default_account.id, message, folder, 13)
        update_message_metadata(db.session, default_account, message, False)
        db.session.commit()
        folder_map[name] = folder
        message_map[name] = message
    return folder_map, message_map


def add_inbox_label(db, default_account, message):
    assert len(message.imapuids) == 1
    imapuid = message.imapuids[0]
    assert {c.name for c in imapuid.categories} == {"all"}
    imapuid.update_labels(["\\Inbox"])
    db.session.commit()
    assert {c.name for c in imapuid.categories} == {"all", "inbox"}
    update_message_metadata(db.session, default_account, message, False)
    db.session.commit()
    return message


def add_custom_label(db, default_account, message):
    assert len(message.imapuids) == 1
    imapuid = message.imapuids[0]
    existing = [c.name for c in imapuid.categories][0]
    imapuid.update_labels(["<3"])
    db.session.commit()
    assert {c.name for c in imapuid.categories} == {existing, ""}
    update_message_metadata(db.session, default_account, message, False)
    db.session.commit()
    return message


@pytest.mark.parametrize("label", ["all", "trash", "spam"])
def test_validation(
    db, api_client, default_account, folder_and_message_maps, label
):
    folder_map, message_map = folder_and_message_maps

    message = message_map[label]
    resp_data = api_client.get_data(f"/messages/{message.public_id}")
    labels = resp_data["labels"]
    assert len(labels) == 1
    assert labels[0]["name"] == label
    existing_label = labels[0]["id"]

    # Adding more than one mutually exclusive label is not allowed.
    # For example, adding 'trash' and 'spam'.
    # (Adding one is okay because it's simply replaced).
    labels_to_add = []
    for key in message_map:
        if key == label:
            continue
        labels_to_add += [folder_map[key].category.public_id]

    response = api_client.put_data(
        f"/messages/{message.public_id}", {"label_ids": labels_to_add}
    )
    resp_data = json.loads(response.data)
    assert response.status_code == 400
    assert resp_data.get("type") == "invalid_request_error"

    response = api_client.put_data(
        f"/messages/{message.public_id}",
        {"label_ids": labels_to_add + [existing_label]},
    )
    resp_data = json.loads(response.data)
    assert response.status_code == 400
    assert resp_data.get("type") == "invalid_request_error"

    # Removing all labels is not allowed, because this will remove
    # the required label (one of 'all'/ 'trash'/ 'spam') too.
    response = api_client.put_data(
        f"/messages/{message.public_id}", {"label_ids": []}
    )
    resp_data = json.loads(response.data)
    assert response.status_code == 400
    assert resp_data.get("type") == "invalid_request_error"


@pytest.mark.parametrize("label", ["all", "trash", "spam"])
def test_adding_a_mutually_exclusive_label_replaces_the_other(
    db, api_client, default_account, folder_and_message_maps, label
):
    # Verify a Gmail message can only have ONE of the 'all', 'trash', 'spam'
    # labels at a time. We specifically test that adding 'all'/ 'trash'/ 'spam'
    # to a message in one of the other two folders *replaces*
    # the existing label with the label being added.
    folder_map, message_map = folder_and_message_maps
    label_to_add = folder_map[label]

    for key in message_map:
        if key == label:
            continue

        message = message_map[key]
        resp_data = api_client.get_data(f"/messages/{message.public_id}")
        labels = resp_data["labels"]
        assert len(labels) == 1
        assert labels[0]["name"] == key
        existing_label = labels[0]["id"]

        # Adding 'all'/ 'trash'/ 'spam' removes the existing one,
        # irrespective of whether it's provided in the request or not.
        response = api_client.put_data(
            f"/messages/{message.public_id}",
            {"label_ids": [label_to_add.category.public_id, existing_label]},
        )
        labels = json.loads(response.data)["labels"]
        assert len(labels) == 1
        assert labels[0]["name"] == label


@pytest.mark.parametrize("label", ["trash", "spam"])
def test_adding_trash_or_spam_removes_inbox(
    db, api_client, default_account, folder_and_message_maps, label
):
    # Verify a Gmail message in 'trash', 'spam' cannot have 'inbox'.
    # We specifically test that adding 'trash'/ 'spam' to a message with 'inbox'
    # removes it.
    folder_map, message_map = folder_and_message_maps

    message = message_map["all"]
    add_inbox_label(db, default_account, message)
    resp_data = api_client.get_data(f"/messages/{message.public_id}")
    labels = resp_data["labels"]
    assert len(labels) == 2
    assert {lbl["name"] for lbl in labels} == {"all", "inbox"}

    # Adding 'trash'/ 'spam' removes 'inbox' (and 'all'),
    # irrespective of whether it's provided in the request or not.
    label_to_add = folder_map[label]
    response = api_client.put_data(
        f"/messages/{message.public_id}",
        {
            "label_ids": [label_to_add.category.public_id]
            + [lbl["id"] for lbl in labels]
        },
    )
    labels = json.loads(response.data)["labels"]
    assert len(labels) == 1
    assert labels[0]["name"] == label


@pytest.mark.parametrize("label", ["all", "trash", "spam"])
def test_adding_a_mutually_exclusive_label_does_not_affect_custom_labels(
    db, api_client, default_account, folder_and_message_maps, label
):
    folder_map, message_map = folder_and_message_maps
    label_to_add = folder_map[label]

    for key in message_map:
        if key == label:
            continue

        message = message_map[key]
        add_custom_label(db, default_account, message)
        resp_data = api_client.get_data(f"/messages/{message.public_id}")
        labels = resp_data["labels"]
        assert len(labels) == 2
        assert key in [lbl["name"] for lbl in labels]
        assert "<3" in [lbl["display_name"] for lbl in labels]

        # Adding only 'all'/ 'trash'/ 'spam' does not change custom labels.
        response = api_client.put_data(
            f"/messages/{message.public_id}",
            {
                "label_ids": [label_to_add.category.public_id]
                + [lbl["id"] for lbl in labels]
            },
        )
        labels = json.loads(response.data)["labels"]
        assert len(labels) == 2
        assert label in [lbl["name"] for lbl in labels]
        assert "<3" in [lbl["display_name"] for lbl in labels]


@pytest.mark.parametrize("label", ["all", "trash", "spam"])
def test_adding_inbox_adds_all_and_removes_trash_spam(
    db, api_client, default_account, folder_and_message_maps, label
):
    # Verify a Gmail message in 'trash', 'spam' cannot have 'inbox'.
    # This time we test that adding 'inbox' to a message in the 'trash'/ 'spam'
    # moves it to 'all' in addition to adding 'inbox'.
    folder_map, message_map = folder_and_message_maps  # noqa: F841

    message = message_map[label]
    resp_data = api_client.get_data(f"/messages/{message.public_id}")
    labels = resp_data["labels"]
    assert len(labels) == 1
    assert labels[0]["name"] == label
    existing_label = labels[0]["id"]

    inbox_label = add_fake_label(db.session, default_account, "Inbox", "inbox")
    db.session.commit()

    # Adding 'inbox' adds 'all', replacing 'trash'/ 'spam' if needed.
    response = api_client.put_data(
        f"/messages/{message.public_id}",
        {"label_ids": [inbox_label.category.public_id, existing_label]},
    )
    db.session.commit()
    labels = json.loads(response.data)["labels"]
    assert len(labels) == 2
    assert {lbl["name"] for lbl in labels} == {"all", "inbox"}


@pytest.mark.parametrize("label", ["all", "trash", "spam"])
def test_adding_a_custom_label_preserves_other_labels(
    db, api_client, default_account, folder_and_message_maps, label
):
    folder_map, message_map = folder_and_message_maps  # noqa: F841

    message = message_map[label]
    resp_data = api_client.get_data(f"/messages/{message.public_id}")
    labels = resp_data["labels"]
    assert len(labels) == 1
    assert labels[0]["name"] == label
    existing_label = labels[0]["id"]

    custom_label = add_fake_label(db.session, default_account, "<3", None)
    db.session.commit()

    # Adding only a custom label does not move a message to a different folder
    # i.e. does not change its 'all'/ 'trash'/ 'spam' labels.
    response = api_client.put_data(
        f"/messages/{message.public_id}",
        {"label_ids": [custom_label.category.public_id, existing_label]},
    )
    labels = json.loads(response.data)["labels"]
    assert len(labels) == 2
    assert {lbl["name"] for lbl in labels} == {label, None}
    assert "<3" in [lbl["display_name"] for lbl in labels]


@pytest.mark.parametrize("label", ["all", "trash", "spam"])
def test_removing_a_mutually_exclusive_label_does_not_orphan_a_message(
    db, api_client, default_account, folder_and_message_maps, label
):
    folder_map, message_map = folder_and_message_maps  # noqa: F841

    message = message_map[label]
    resp_data = api_client.get_data(f"/messages/{message.public_id}")
    labels = resp_data["labels"]
    assert len(labels) == 1
    assert labels[0]["name"] == label

    custom_label = add_fake_label(db.session, default_account, "<3", None)
    db.session.commit()

    # Removing a message's ONLY folder "label" does not remove it.
    # Gmail messages MUST belong to one of 'all'/ 'trash'/ 'spam'.
    response = api_client.put_data(
        f"/messages/{message.public_id}",
        {"label_ids": [custom_label.category.public_id]},
    )
    labels = json.loads(response.data)["labels"]
    assert len(labels) == 2
    assert {lbl["name"] for lbl in labels} == {label, None}
    assert "<3" in [lbl["display_name"] for lbl in labels]


@pytest.fixture
def imapuid_with_folder_list_labels(db, default_account, imapuid):
    """
    Return an IMAP UID with labels as the folder list creates them: a system
    label with its Gmail folder name, and a custom label without a canonical
    name.
    """
    imapuid.labels.add(
        add_fake_label(
            db.session, default_account, "[Gmail]/Important", "important"
        )
    )
    imapuid.labels.add(add_fake_label(db.session, default_account, "<3", None))
    db.session.commit()
    return imapuid


def test_imap_labels_match_labels_of_folder_list(
    db, imapuid_with_folder_list_labels
) -> None:
    imapuid = imapuid_with_folder_list_labels
    label_item_ids = {label_item.id for label_item in imapuid.labelitems}
    gmail_labels = ["\\Important", "<3", "\\Starred"]

    assert imapuid.compare_labels(gmail_labels) == (set(), set())
    imapuid.update_labels(gmail_labels)
    db.session.commit()

    assert {
        label_item.id for label_item in imapuid.labelitems
    } == label_item_ids


@pytest.mark.parametrize(
    ("gmail_labels", "fetched_only_label_names", "saved_only_label_names"),
    [
        (["\\Important"], set(), {"<3"}),
        (["\\Important", "<3", "\\Inbox"], {"inbox"}, set()),
        (["\\Important", "<4"], {"<4"}, {"<3"}),
    ],
)
def test_imap_labels_differ_from_labels_of_folder_list(
    db,
    imapuid_with_folder_list_labels,
    gmail_labels,
    fetched_only_label_names,
    saved_only_label_names,
) -> None:
    imapuid = imapuid_with_folder_list_labels

    assert imapuid.compare_labels(gmail_labels) == (
        fetched_only_label_names,
        saved_only_label_names,
    )
    imapuid.update_labels(gmail_labels)
    db.session.commit()

    assert imapuid.compare_labels(gmail_labels) == (set(), set())


def test_numeric_imap_label_matches_label_row(
    db, default_account, imapuid
) -> None:
    imapuid.labels.add(add_fake_label(db.session, default_account, "42", None))
    db.session.commit()

    # IMAPClient parses numeric labels into integers.
    assert imapuid.compare_labels([42]) == (set(), set())


@pytest.mark.parametrize(
    "gmail_label",
    [
        "x" * (MAX_INDEXABLE_LENGTH + 1),
        "Trailing space ",
        # The truncated name ends with a space.
        "x" * (MAX_INDEXABLE_LENGTH - 1) + " y",
    ],
)
def test_imap_label_matches_label_row_with_sanitized_name(
    db, imapuid, gmail_label
) -> None:
    imapuid.update_labels([gmail_label])
    db.session.commit()

    # Label rows store each name sanitized.
    assert imapuid.compare_labels([gmail_label]) == (set(), set())


def test_imap_labels_remove_duplicate_label_rows(
    db, default_account, imapuid
) -> None:
    label = add_fake_label(db.session, default_account, "<3", None)
    # Syncback and mail sync can both save a row for the same label.
    for _ in range(2):
        imapuid.labelitems.add(LabelItem(label=label))
    db.session.commit()
    assert len(imapuid.labelitems) == 2

    imapuid.update_labels([])
    db.session.commit()

    assert not imapuid.labelitems

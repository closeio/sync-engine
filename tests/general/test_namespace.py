import random

import pytest
from freezegun import freeze_time
from pytest import fixture  # noqa: PT013
from requests import Response

from inbox.models.namespace import Namespace
from tests.util.base import (
    add_fake_calendar,
    add_fake_contact,
    add_fake_event,
    add_fake_folder,
    add_fake_gmail_account,
    add_fake_imapuid,
    add_fake_message,
    add_fake_msg_with_calendar_part,
    add_fake_thread,
    add_generic_imap_account,
)


@fixture
def patch_requests_throttle(monkeypatch) -> None:
    def get(*args, **kwargs):
        resp = Response()
        resp.status_code = 500

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: get())


@fixture
def patch_requests_no_throttle(monkeypatch) -> None:
    def get(*args, **kwargs):
        resp = Response()
        resp.status_code = 500

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: get())


def random_range(start, end):
    return list(range(random.randrange(start, end)))


def add_completely_fake_account(db, email="test@nylas.com"):
    fake_account = add_fake_gmail_account(db.session, email_address=email)
    calendar = add_fake_calendar(db.session, fake_account.namespace.id)
    for i in random_range(1, 10):
        add_fake_event(
            db.session,
            fake_account.namespace.id,
            calendar=calendar,
            title=str(i),
        )

    # Add fake Threads, Messages and ImapUids.
    folder = add_fake_folder(db.session, fake_account)
    for _ in random_range(1, 4):
        th = add_fake_thread(db.session, fake_account.namespace.id)

        for _ in random_range(1, 3):
            msg = add_fake_msg_with_calendar_part(
                db.session, fake_account, "fake part", thread=th
            )
            db.session.add(msg)
            db.session.flush()

            for k in random_range(1, 2):
                add_fake_imapuid(
                    db.session,
                    fake_account.id,
                    msg,
                    folder,
                    int(str(msg.id) + str(k)),
                )
    # Add fake contacts
    for i in random_range(1, 5):
        add_fake_contact(db.session, fake_account.namespace.id, uid=str(i))

    db.session.commit()

    return fake_account


def test_get_accounts_to_delete(db) -> None:
    from inbox.models import Account
    from inbox.models.util import get_accounts_to_delete

    existing_account_count = db.session.query(Account.id).count()

    accounts = []
    email = "test{}@nylas.com"
    for i in range(1, 6):
        account = add_completely_fake_account(db, email.format(i))
        accounts.append(account)

    # Ensure all of the accounts have been created successfully
    assert db.session.query(Account.id).count() == (existing_account_count + 5)

    # get_accounts_to_delete() with no accounts marked as deleted
    accounts_to_delete = get_accounts_to_delete(0)
    assert len(accounts_to_delete) == 0

    # get_accounts_to_delete() with one account marked as deleted
    accounts[0].mark_for_deletion()
    db.session.commit()

    accounts_to_delete = get_accounts_to_delete(0)
    assert len(accounts_to_delete) == 1

    # get_accounts_to_delete() with more than one account marked as deleted
    for i in range(1, 4):
        accounts[i].mark_for_deletion()
    db.session.commit()

    accounts_to_delete = get_accounts_to_delete(0)
    assert len(accounts_to_delete) == 4


@pytest.mark.usefixtures("blockstore_backend")
@pytest.mark.parametrize("blockstore_backend", ["disk", "s3"], indirect=True)
def test_bulk_namespace_deletion(db) -> None:
    from inbox.models import Account
    from inbox.models.util import (
        batch_delete_namespaces,
        get_accounts_to_delete,
    )

    db.session.query(Account).delete(synchronize_session=False)
    db.session.commit()
    assert db.session.query(Account.id).count() == 0

    # Add 5 accounts
    account_1 = add_completely_fake_account(db)
    account_1_id = account_1.id

    account_2 = add_completely_fake_account(db, "test2@nylas.com")
    account_2_id = account_2.id

    account_3 = add_completely_fake_account(db, "test3@nylas.com")
    account_3_id = account_3.id

    account_4 = add_completely_fake_account(db, "test4@nylas.com")
    account_4_id = account_4.id

    add_completely_fake_account(db, "test5@nylas.com")

    # Ensure all of the accounts have been created successfully
    assert db.session.query(Account).count() == 5

    # batch_delete_namespaces() with no accounts marked as deleted
    to_delete = get_accounts_to_delete(0)
    batch_delete_namespaces(to_delete)
    assert len(db.session.query(Account.id).all()) == 5

    # batch_delete_namespaces() with one account marked as deleted
    account_1.mark_for_deletion()
    db.session.commit()

    to_delete = get_accounts_to_delete(0)
    batch_delete_namespaces(to_delete)

    alive_accounts = db.session.query(Account.id).all()
    assert len(alive_accounts) == 4
    assert account_1_id not in alive_accounts

    # batch_delete_namespaces() with more than one account marked as deleted
    account_2.mark_for_deletion()
    account_3.mark_for_deletion()
    account_4.mark_for_deletion()
    db.session.commit()

    to_delete = get_accounts_to_delete(0)
    batch_delete_namespaces(to_delete)

    alive_accounts = db.session.query(Account.id).all()
    assert len(alive_accounts) == 1
    assert account_4_id not in alive_accounts
    assert account_3_id not in alive_accounts
    assert account_2_id not in alive_accounts


@freeze_time("2016-02-02 11:01:34")
def test_deletion_no_throttle(db, patch_requests_no_throttle) -> None:
    from inbox.models import Account
    from inbox.models.util import (
        batch_delete_namespaces,
        get_accounts_to_delete,
    )

    new_accounts = set()
    account_1 = add_completely_fake_account(db)
    new_accounts.add(account_1.id)

    account_2 = add_completely_fake_account(db, "test2@nylas.com")
    new_accounts.add(account_2.id)

    account_1.mark_for_deletion()
    account_2.mark_for_deletion()
    db.session.commit()

    to_delete = get_accounts_to_delete(0)
    batch_delete_namespaces(to_delete, throttle=True)

    alive_accounts = db.session.query(Account.id).all()

    # Ensure the two accounts we added were deleted
    assert new_accounts - set(alive_accounts) == new_accounts


@freeze_time("2016-02-02 11:01:34")
def test_deletion_metric_throttle(db, patch_requests_throttle) -> None:
    from inbox.models import Account
    from inbox.models.util import (
        batch_delete_namespaces,
        get_accounts_to_delete,
    )

    account_1 = add_completely_fake_account(db)
    account_1_id = account_1.id

    account_2 = add_completely_fake_account(db, "test2@nylas.com")
    account_2_id = account_2.id

    account_1.mark_for_deletion()
    account_2.mark_for_deletion()
    db.session.commit()

    to_delete = get_accounts_to_delete(0)
    batch_delete_namespaces(to_delete, throttle=True)

    alive_accounts = [acc.id for acc in db.session.query(Account).all()]

    # Ensure the two accounts we added are still present
    assert account_1_id in alive_accounts
    assert account_2_id in alive_accounts


@freeze_time("2016-02-02 01:01:34")
def test_deletion_time_throttle(db, patch_requests_no_throttle) -> None:
    from inbox.models import Account
    from inbox.models.util import (
        batch_delete_namespaces,
        get_accounts_to_delete,
    )

    account_1 = add_completely_fake_account(db, "test5@nylas.com")
    account_1_id = account_1.id

    account_2 = add_completely_fake_account(db, "test6@nylas.com")
    account_2_id = account_2.id

    account_1.mark_for_deletion()
    account_2.mark_for_deletion()
    db.session.commit()

    to_delete = get_accounts_to_delete(0)
    batch_delete_namespaces(to_delete, throttle=True)

    alive_accounts = [acc.id for acc in db.session.query(Account).all()]

    # Ensure the two accounts we added are still present
    assert account_1_id in alive_accounts
    assert account_2_id in alive_accounts


def test_namespace_deletion(db, default_account) -> None:
    from inbox.models import Account, Message, Thread
    from inbox.models.util import delete_namespace

    models = [Thread, Message]

    namespace = default_account.namespace
    namespace_id = namespace.id
    account_id = default_account.id

    account = db.session.query(Account).get(account_id)
    assert account

    thread = add_fake_thread(db.session, namespace_id)

    message = add_fake_message(db.session, namespace_id, thread)

    for m in models:
        c = db.session.query(m).filter(m.namespace_id == namespace_id).count()
        print("count for", m, ":", c)  # noqa: T201
        assert c != 0

    fake_account = add_generic_imap_account(db.session)
    fake_account_id = fake_account.id

    assert fake_account_id != account.id
    assert fake_account.namespace.id != namespace_id

    thread = add_fake_thread(db.session, fake_account.namespace.id)
    thread_id = thread.id

    message = add_fake_message(db.session, fake_account.namespace.id, thread)
    message_id = message.id

    assert (
        len(
            db.session.query(Namespace)
            .filter(Namespace.id == namespace_id)
            .all()
        )
        > 0
    )

    # Delete namespace, verify data corresponding to this namespace /only/
    # is deleted

    account = (
        db.session.query(Account)
        .join(Namespace)
        .filter(Namespace.id == namespace_id)
        .one()
    )
    account.mark_for_deletion()

    delete_namespace(namespace_id)
    db.session.commit()

    assert (
        len(
            db.session.query(Namespace)
            .filter(Namespace.id == namespace_id)
            .all()
        )
        == 0
    )

    account = db.session.query(Account).get(account_id)
    assert not account

    for m in models:
        assert (
            db.session.query(m).filter(m.namespace_id == namespace_id).count()
            == 0
        )

    fake_account = db.session.query(Account).get(fake_account_id)
    assert fake_account

    thread = db.session.query(Thread).get(thread_id)
    message = db.session.query(Message).get(message_id)
    assert thread
    assert message


def test_namespace_delete_cascade(db, default_account) -> None:
    from inbox.models import Account, Message, Thread

    models = [Thread, Message]

    namespace = default_account.namespace
    namespace_id = namespace.id
    account_id = default_account.id

    account = db.session.query(Account).get(account_id)
    assert account

    thread = add_fake_thread(db.session, namespace_id)

    add_fake_message(db.session, namespace_id, thread)

    for m in models:
        c = db.session.query(m).filter(m.namespace_id == namespace_id).count()
        print("count for", m, ":", c)  # noqa: T201
        assert c != 0

    fake_account = add_generic_imap_account(db.session)
    fake_account_id = fake_account.id

    assert fake_account_id != account.id
    assert fake_account.namespace.id != namespace_id

    thread = add_fake_thread(db.session, fake_account.namespace.id)

    add_fake_message(db.session, fake_account.namespace.id, thread)

    assert (
        len(
            db.session.query(Namespace)
            .filter(Namespace.id == namespace_id)
            .all()
        )
        > 0
    )

    # This test is separate from test_namespace_deletion because we want to
    # do a raw SQLAlchemy delete rather than using delete_namespace, which does
    # a bunch of extra work to ensure that objects associated with a Namespace
    # are actually deleted.
    db.session.query(Namespace).filter(Namespace.id == namespace_id).delete()
    db.session.commit()

    assert (
        len(
            db.session.query(Namespace)
            .filter(Namespace.id == namespace_id)
            .all()
        )
        == 0
    )


def test_fake_accounts(empty_db) -> None:
    from inbox.models import (
        Account,
        Block,
        Contact,
        Event,
        Message,
        Secret,
        Thread,
        Transaction,
    )
    from inbox.models.backends.imap import ImapUid
    from inbox.models.util import delete_namespace

    models = [Thread, Message, Event, Transaction, Contact, Block]

    db = empty_db
    account = add_completely_fake_account(db)

    for m in models:
        c = (
            db.session.query(m)
            .filter(m.namespace_id == account.namespace.id)
            .count()
        )
        assert c != 0

    assert db.session.query(ImapUid).count() != 0
    assert db.session.query(Secret).count() != 0
    assert (
        db.session.query(Account).filter(Account.id == account.id).count() == 1
    )

    # Try the dry-run mode:
    account.mark_for_deletion()
    delete_namespace(account.namespace.id, dry_run=True)

    for m in models:
        c = (
            db.session.query(m)
            .filter(m.namespace_id == account.namespace.id)
            .count()
        )
        assert c != 0

    assert (
        db.session.query(Account).filter(Account.id == account.id).count() != 0
    )

    assert db.session.query(Secret).count() != 0
    assert db.session.query(ImapUid).count() != 0

    # Now delete the account for reals.
    delete_namespace(account.namespace.id)

    for m in models:
        c = (
            db.session.query(m)
            .filter(m.namespace_id == account.namespace.id)
            .count()
        )
        assert c == 0

    assert (
        db.session.query(Account).filter(Account.id == account.id).count() == 0
    )

    assert db.session.query(Secret).count() == 0
    assert db.session.query(ImapUid).count() == 0


def test_multiple_fake_accounts(empty_db) -> None:
    # Add three fake accounts, check that removing one doesn't affect
    # the two others.
    from inbox.models import (
        Block,
        Contact,
        Event,
        Message,
        Secret,
        Thread,
        Transaction,
    )
    from inbox.models.util import delete_namespace

    db = empty_db
    accounts = []
    accounts.append(add_completely_fake_account(db, "test1@nylas.com"))
    accounts.append(add_completely_fake_account(db, "test2@nylas.com"))

    # Count secrets and authcredentials now. We can't do it after adding
    # the third account because our object model is a bit cumbersome.
    secret_count = db.session.query(Secret).count()
    assert secret_count != 0

    accounts.append(add_completely_fake_account(db, "test3@nylas.com"))

    stats = {}
    models = [Thread, Message, Event, Transaction, Contact, Block]

    for account in accounts:
        stats[account.email_address] = {}
        for model in models:
            clsname = model.__name__
            stats[account.email_address][clsname] = (
                db.session.query(model)
                .filter(model.namespace_id == account.namespace.id)
                .count()
            )

    # now delete the third account.
    last_namespace_id = accounts[2].namespace.id
    accounts[2].mark_for_deletion()

    delete_namespace(last_namespace_id)

    for account in accounts[:2]:
        for model in models:
            clsname = model.__name__
            assert (
                stats[account.email_address][clsname]
                == db.session.query(model)
                .filter(model.namespace_id == account.namespace.id)
                .count()
            )

    # check that no model from the last account is present.
    for model in models:
        clsname = model.__name__
        assert (
            db.session.query(model)
            .filter(model.namespace_id == last_namespace_id)
            .count()
            == 0
        )

    # check that we didn't delete a secret that wasn't ours.
    assert db.session.query(Secret).count() == secret_count

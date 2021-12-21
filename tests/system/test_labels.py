from __future__ import absolute_import, print_function

# gmail-specific label handling tests.
import random
from datetime import datetime

import pytest

from inbox.crispin import writable_connection_pool
from inbox.mailsync.backends.imap.generic import uidvalidity_cb
from inbox.models import Account
from inbox.models.session import session_scope

from .conftest import gmail_accounts, timeout_loop


@timeout_loop("tag_add")
def wait_for_tag(client, thread_id, tagname):
    thread = client.threads.find(thread_id)
    tags = [tag["name"] for tag in thread.tags]
    return tagname in tags


@timeout_loop("tag_remove")
def wait_for_tag_removal(client, thread_id, tagname):
    thread = client.threads.find(thread_id)
    tags = [tag["name"] for tag in thread.tags]
    return tagname not in tags


@pytest.mark.parametrize("client", gmail_accounts)
def test_gmail_labels(client):
    # test case: create a label on the gmail account
    # apply it to a thread. Check that it gets picked up.
    # Remove it. Check that it gets picked up.
    thread = random.choice(client.threads.all())

    account = None
    with session_scope() as db_session:
        account = (
            db_session.query(Account)
            .filter_by(email_address=client.email_address)
            .one()
        )

        connection_pool = writable_connection_pool(account.id, pool_size=1)
        with connection_pool.get() as crispin_client:
            label_name = "custom-label" + datetime.now().strftime("%s.%f")
            print("Label:", label_name)

            folder_name = crispin_client.folder_names()["all"]
            crispin_client.select_folder(folder_name, uidvalidity_cb)

            print("Subject :", thread.subject)
            uids = crispin_client.search_uids(["SUBJECT", thread.subject])
            g_thrid = list(crispin_client.g_metadata(uids).items())[0][1].thrid

            crispin_client.add_label(g_thrid, label_name)
            wait_for_tag(client, thread.id, label_name)

            draft = client.drafts.create(
                to=[{"name": "Nylas SelfSend", "email": client.email_address}],
                body="Blah, replying to message",
                subject=thread.subject,
            )
            draft.send()

            crispin_client.remove_label(g_thrid, label_name)
            wait_for_tag_removal(client, thread.id, label_name)


if __name__ == "__main__":
    pytest.main([__file__])

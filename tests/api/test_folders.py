# flake8: noqa: F401, F811
import time
from unittest import mock

from inbox.util.testutils import mock_imapclient  # noqa

from tests.util.base import add_fake_category, add_fake_folder


# Check that folders of the form INBOX.A.B get converted by the API
# to A/B.
def test_folder_stripping(db, generic_account, imap_api_client):
    # Check that regular IMAP paths get converted to unix-style paths
    generic_account.folder_separator = "."
    folder = add_fake_folder(db.session, generic_account)
    add_fake_category(db.session, generic_account.namespace.id, "INBOX.Red.Carpet")

    r = imap_api_client.get_data("/folders")
    for folder in r:
        if "Carpet" in folder["display_name"]:
            assert folder["display_name"] == "INBOX/Red/Carpet"

    # Check that if we define an account-level prefix, it gets stripped
    # from the API response.
    generic_account.folder_prefix = "INBOX."
    db.session.commit()

    r = imap_api_client.get_data("/folders")
    for folder in r:
        if "Carpet" in folder["display_name"]:
            assert folder["display_name"] == "Red/Carpet"

    # Test again with a prefix without integrated separator:
    generic_account.folder_prefix = "INBOX"
    db.session.commit()

    r = imap_api_client.get_data("/folders")
    for folder in r:
        if "Carpet" in folder["display_name"]:
            assert folder["display_name"] == "Red/Carpet"


# This test is kind of complicated --- basically we mock
# the output of the IMAP NAMESPACE command to check that
# we are correctly translating Unix-style paths to IMAP
# paths.
def test_folder_name_translation(
    empty_db, generic_account, imap_api_client, mock_imapclient, monkeypatch
):
    from inbox.transactions.actions import SyncbackService

    syncback = SyncbackService(
        syncback_id=0, process_number=0, total_processes=1, num_workers=2
    )

    imap_namespaces = ((("INBOX.", "."),),)
    mock_imapclient.create_folder = mock.Mock()
    mock_imapclient.namespace = mock.Mock(return_value=imap_namespaces)

    folder_list = [(("\\HasChildren",), ".", "INBOX")]
    mock_imapclient.list_folders = mock.Mock(return_value=folder_list)
    mock_imapclient.has_capability = mock.Mock(return_value=True)

    folder_prefix, folder_separator = imap_namespaces[0][0]
    generic_account.folder_prefix = folder_prefix
    generic_account.folder_separator = folder_separator
    empty_db.session.commit()

    folder_json = {"display_name": "Taxes/Accounting"}
    imap_api_client.post_data("/folders", folder_json)

    syncback._process_log()
    syncback._restart_workers()
    time.sleep(1)
    while not syncback.task_queue.empty():
        time.sleep(1)
    time.sleep(1)
    mock_imapclient.create_folder.assert_called_with("INBOX.Taxes.Accounting")

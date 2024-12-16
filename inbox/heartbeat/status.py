import time
from collections import namedtuple

from inbox.heartbeat.config import ALIVE_EXPIRY
from inbox.heartbeat.store import HeartbeatStore
from inbox.logging import get_logger

log = get_logger()


# More lightweight statuses (dead/alive signals only) - placeholder name Pings
AccountPing = namedtuple("AccountPing", ["id", "folders"])
FolderPing = namedtuple("FolderPing", ["id", "alive", "timestamp"])


def get_ping_status(  # noqa: ANN201
    account_ids, host=None, port: int = 6379, threshold=ALIVE_EXPIRY
):
    # Query the indexes and not the per-folder info for faster lookup.
    store = HeartbeatStore.store(host, port)
    now = time.time()
    expiry = now - threshold
    if len(account_ids) == 1:
        # Get a single account's heartbeat
        account_id = account_ids[0]
        folder_heartbeats = store.get_account_folders(account_id)
        folders = [
            FolderPing(int(aid), ts > expiry, ts)
            for (aid, ts) in folder_heartbeats
        ]
        account = AccountPing(account_id, folders)
        return {account_id: account}
    else:
        accounts = {}
        # grab the folders from all accounts in one batch
        all_folder_heartbeats = store.get_accounts_folders(account_ids)
        for account_id in account_ids:
            account_id = int(account_id)
            folder_heartbeats = all_folder_heartbeats[account_id]
            folders = [
                FolderPing(int(aid), ts > expiry, ts)
                for (aid, ts) in folder_heartbeats
            ]
            account = AccountPing(account_id, folders)
            accounts[account_id] = account

        return accounts


def clear_heartbeat_status(  # noqa: ANN201
    account_id, folder_id=None, device_id=None
):
    # Clears the status for the account, folder and/or device.
    # Returns the number of folders cleared.
    store = HeartbeatStore.store()
    n = store.remove_folders(account_id, folder_id, device_id)
    return n

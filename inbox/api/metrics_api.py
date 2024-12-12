from collections import defaultdict
from operator import itemgetter
from typing import Any

from flask import Blueprint, request
from sqlalchemy.orm import joinedload
from sqlalchemy.orm.exc import NoResultFound

from inbox.api.err import InputError
from inbox.api.kellogs import APIEncoder
from inbox.events.remote_sync import EVENT_SYNC_FOLDER_ID
from inbox.heartbeat.status import get_ping_status
from inbox.logging import get_logger
from inbox.models import Account, Calendar, Folder, Namespace
from inbox.models.backends.generic import GenericAccount
from inbox.models.backends.imap import ImapAccount, ImapFolderSyncStatus
from inbox.models.session import global_session_scope

log = get_logger()

app = Blueprint("metrics_api", __name__, url_prefix="/metrics")


def _get_calendar_data(db_session, namespace):
    calendars = db_session.query(Calendar)
    if namespace:
        calendars = calendars.filter_by(namespace_id=namespace.id)

    calendars = calendars.options(
        joinedload(Calendar.namespace)
        .load_only(Namespace.account_id)
        .noload(Namespace.account)
    )

    calendar_data = defaultdict(list)
    for calendar in calendars:
        account_id = calendar.namespace.account_id

        state = None
        if calendar.can_sync():
            if calendar.last_synced:
                state = "running"
            else:
                state = "initial"

        calendar_data[account_id].append(
            {
                "uid": calendar.uid,
                "name": calendar.name,
                "last_synced": calendar.last_synced,
                "state": state,
            }
        )

    return calendar_data


def _get_folder_data(db_session, accounts):
    folder_sync_statuses = db_session.query(ImapFolderSyncStatus)
    # This assumes that the only cases for metrics we have is 1) fetching
    # metrics for a specific account, and 2) fetching metrics for all accounts.
    if len(accounts) == 1:
        folder_sync_statuses = folder_sync_statuses.filter(
            ImapFolderSyncStatus.account_id == accounts[0].id
        )
    folder_sync_statuses = folder_sync_statuses.join(Folder).with_entities(
        ImapFolderSyncStatus.account_id,
        ImapFolderSyncStatus.folder_id,
        Folder.name,
        ImapFolderSyncStatus.state,
        ImapFolderSyncStatus._metrics,
    )

    folder_data: defaultdict[int, dict[int, dict[str, Any]]] = defaultdict(
        dict
    )

    for folder_sync_status in folder_sync_statuses:
        (account_id, folder_id, folder_name, state, metrics) = (
            folder_sync_status
        )
        folder_data[account_id][folder_id] = {
            "remote_uid_count": metrics.get("remote_uid_count"),
            "download_uid_count": metrics.get("download_uid_count"),
            "state": state,
            "name": folder_name,
            "alive": False,
            "heartbeat_at": None,
            "run_state": metrics.get("run_state"),
            "sync_error": metrics.get("sync_error"),
        }
    return folder_data


@app.route("/")
def index():  # noqa: ANN201
    with global_session_scope() as db_session:
        if "namespace_id" in request.args:
            try:
                namespace = (
                    db_session.query(Namespace)
                    .filter(
                        Namespace.public_id == request.args["namespace_id"]
                    )
                    .one()
                )
            except NoResultFound:
                return APIEncoder().jsonify([])
        else:
            namespace = None

        accounts = db_session.query(ImapAccount).with_polymorphic(
            [GenericAccount]
        )

        if namespace:
            accounts = accounts.filter(Account.namespace == namespace)
        else:
            # Get all account IDs that aren't deleted
            account_ids = [
                result[0]
                for result in db_session.query(
                    ImapAccount.id, ImapAccount._sync_status
                )
                if result[1].get("sync_disabled_reason") != "account deleted"
            ]

            # This is faster than fetching all accounts.
            accounts = accounts.filter(ImapAccount.id.in_(account_ids))

        accounts = list(accounts)

        folder_data = _get_folder_data(db_session, accounts)
        calendar_data = _get_calendar_data(db_session, namespace)
        heartbeat = get_ping_status(account_ids=[acc.id for acc in accounts])

        data = []

        for account in accounts:
            if account.id in heartbeat:
                account_heartbeat = heartbeat[account.id]
                account_folder_data = folder_data[account.id]
                account_calendar_data = calendar_data[account.id]

                events_alive = False

                for folder_status in account_heartbeat.folders:
                    folder_status_id = int(folder_status.id)
                    if folder_status_id in account_folder_data:
                        account_folder_data[folder_status_id].update(
                            {
                                "alive": folder_status.alive,
                                "heartbeat_at": folder_status.timestamp,
                            }
                        )
                    elif folder_status_id == EVENT_SYNC_FOLDER_ID:
                        events_alive = folder_status.alive

                email_alive = all(
                    f["alive"] for f in account_folder_data.values()
                )

                alive = True
                if account.sync_email and not email_alive:
                    alive = False
                if account.sync_events and not events_alive:
                    alive = False

                email_initial_sync = any(
                    f["state"] == "initial"
                    for f in account_folder_data.values()
                )
                events_initial_sync = any(
                    c["state"] == "initial" for c in account_calendar_data
                )
                initial_sync = email_initial_sync or events_initial_sync

                total_uids = sum(
                    f["remote_uid_count"] or 0
                    for f in account_folder_data.values()
                )
                remaining_uids = sum(
                    f["download_uid_count"] or 0
                    for f in account_folder_data.values()
                )
                if total_uids:
                    progress = (
                        100.0 / total_uids * (total_uids - remaining_uids)
                    )
                else:
                    progress = None
            else:
                alive = False
                email_initial_sync = None
                events_initial_sync = None
                initial_sync = None
                progress = None

            sync_status = account.sync_status
            is_running = sync_status["state"] == "running"
            if (
                is_running
                and not sync_status.get("sync_start_time")
                and not sync_status.get("sync_error")
            ):
                sync_status_str = "starting"
            elif is_running and alive:
                if initial_sync:
                    sync_status_str = "initial"
                else:
                    sync_status_str = "running"
            elif is_running:
                # Nylas is syncing, but not all heartbeats are reporting.
                sync_status_str = "delayed"
            else:
                # Nylas is no longer syncing this account.
                sync_status_str = "dead"

            try:
                data.append(
                    {
                        "account_private_id": account.id,
                        "namespace_private_id": account.namespace.id,
                        "account_id": account.public_id,
                        "namespace_id": account.namespace.public_id,
                        "events_alive": events_alive,
                        "email_alive": email_alive,
                        "alive": alive,
                        "email_initial_sync": email_initial_sync,
                        "events_initial_sync": events_initial_sync,
                        "initial_sync": initial_sync,
                        "provider_name": account.provider,
                        "email_address": account.email_address,
                        "folders": sorted(
                            folder_data[account.id].values(),
                            key=itemgetter("name"),
                        ),
                        "calendars": sorted(
                            calendar_data[account.id], key=itemgetter("name")
                        ),
                        "sync_email": account.sync_email,
                        "sync_events": account.sync_events,
                        "sync_status": sync_status_str,
                        "sync_error": sync_status.get("sync_error"),
                        "sync_end_time": sync_status.get("sync_end_time"),
                        "sync_disabled_reason": sync_status.get(
                            "sync_disabled_reason"
                        ),
                        "sync_host": account.sync_host,
                        "progress": progress,
                        "throttled": account.throttled,
                        "created_at": account.created_at,
                        "updated_at": account.updated_at,
                    }
                )
            except Exception:
                log.error(  # noqa: G201
                    "Error while serializing account metrics",
                    account_id=account.id,
                    exc_info=True,
                )

        return APIEncoder().jsonify(data)


@app.route("/global-deltas")
def global_deltas():  # noqa: ANN201
    """
    Return the namespaces with recent transactions.

    Also returns `txnid_start` and `txnid_end`, which can be fed back in as the
    optional `txnid` parameter. `txnid` acts as a cursor, only returning
    namespaces with transactions newer than the given `txnid`.
    """
    from inbox.ignition import redis_txn
    from inbox.models.transaction import TXN_REDIS_KEY

    txnid = request.args.get("txnid", "0")

    try:
        start_pointer = int(txnid)
    except ValueError:
        raise InputError("Invalid cursor parameter")  # noqa: B904

    txns = redis_txn.zrangebyscore(
        TXN_REDIS_KEY,
        f"({start_pointer}",  # don't include start pointer
        "+inf",
        withscores=True,
        score_cast_func=int,
    )
    decoded_txns = [(key.decode(), value) for key, value in txns]

    response = {
        "txnid_start": start_pointer,
        "txnid_end": max([t[1] for t in decoded_txns] or [start_pointer]),
        "deltas": [t[0] for t in decoded_txns],
    }
    return APIEncoder().jsonify(response)

from collections import defaultdict
from flask import Blueprint, jsonify, request
from operator import itemgetter
from sqlalchemy.orm.exc import NoResultFound

from inbox.api.err import NotFoundError
from inbox.api.kellogs import APIEncoder
from inbox.heartbeat.status import get_ping_status
from inbox.models import Folder, Account, Namespace
from inbox.models.backends.generic import GenericAccount
from inbox.models.backends.imap import ImapAccount, ImapFolderSyncStatus
from inbox.models.session import global_session_scope

app = Blueprint(
    'metrics_api',
    __name__,
    url_prefix='/metrics')

@app.route('/')
def index():
    with global_session_scope() as db_session:
        if 'namespace_id' in request.args:
            try:
                namespace = db_session.query(Namespace).filter(
                        Namespace.public_id == request.args['namespace_id']).one()
            except NoResultFound:
                raise NotFoundError("Couldn't find namespace {}".format(request.args['namespace_id']))
        else:
            namespace = None

        accounts = db_session.query(ImapAccount) \
                   .with_polymorphic([GenericAccount])

        if namespace:
            accounts = accounts.filter(Account.namespace == namespace)
        else:
            # Get all account IDs that aren't deleted
            account_ids = [result[0] for result in
                db_session.query(ImapAccount.id, ImapAccount._sync_status)
                if result[1].get('sync_disabled_reason') != 'account deleted']

            # This is faster than fetching all accounts.
            accounts = accounts.filter(ImapAccount.id.in_(account_ids))

        accounts = list(accounts)

        heartbeat = get_ping_status(account_ids=[acc.id for acc in accounts])
        if len(accounts) == 1:
            folder_sync_statuses = db_session.query(ImapFolderSyncStatus). \
                    filter(ImapFolderSyncStatus.account_id==accounts[0].id). \
                    join(Folder)
        else:
            folder_sync_statuses = db_session.query(ImapFolderSyncStatus). \
                    join(Folder)

        data = []

        folder_data = defaultdict(dict)

        for folder_sync_status in folder_sync_statuses:
            metrics = folder_sync_status.metrics
            folder_data[folder_sync_status.account_id][folder_sync_status.folder_id] = {
                'remote_uid_count': metrics.get('remote_uid_count'),
                'download_uid_count': metrics.get('download_uid_count'),
                'state': folder_sync_status.state,
                'name': folder_sync_status.folder.name,
                'alive': False,
                'heartbeat_at': None,
                'run_state': metrics.get('run_state'),
                'sync_error': metrics.get('sync_error'),
            }

        for account in accounts:
            if account.id in heartbeat:
                account_heartbeat = heartbeat[account.id]
                account_folder_data = folder_data[account.id]
                alive = bool(account_heartbeat.folders)
                for folder_status in account_heartbeat.folders:
                    folder_status_id = int(folder_status.id)
                    if folder_status_id in account_folder_data:
                        alive = alive and folder_status.alive
                        account_folder_data[folder_status_id].update({
                            'alive': folder_status.alive,
                            'heartbeat_at': folder_status.timestamp
                        })

                initial_sync = any(f['state'] == 'initial' for f in account_folder_data.values())

                total_uids = sum(f['remote_uid_count'] or 0 for f in account_folder_data.values())
                remaining_uids = sum(f['download_uid_count'] or 0 for f in account_folder_data.values())
                if total_uids:
                    progress = 100. / total_uids * (total_uids - remaining_uids)
                else:
                    progress = None
            else:
                alive = False
                initial_sync = None
                progress = None

            sync_status = account.sync_status
            is_running = sync_status['state'] == 'running'
            if is_running and not sync_status.get('sync_start_time') and not sync_status.get('sync_error'):
                sync_status_str = 'starting'
            elif is_running and alive:
                if initial_sync:
                    sync_status_str = 'initial'
                else:
                    sync_status_str = 'running'
            elif is_running:
                sync_status_str = 'delayed'
            else:
                sync_status_str = 'dead'

            data.append({
                'account_private_id': account.id,
                'namespace_private_id': account.namespace.id,
                'account_id': account.public_id,
                'namespace_id': account.namespace.public_id,
                'alive': alive,
                'initial_sync': initial_sync,
                'provider_name': account.provider,
                'email_address': account.email_address,
                'folders': sorted(folder_data[account.id].values(), key=itemgetter('name')),
                'sync_status': sync_status_str,
                'sync_error': sync_status.get('sync_error'),
                'sync_end_time': sync_status.get('sync_end_time'),
                'sync_host': account.sync_host,
                'progress': progress,
                'throttled': account.throttled,
            })

        return APIEncoder().jsonify(data)

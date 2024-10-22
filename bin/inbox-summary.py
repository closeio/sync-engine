#!/usr/bin/env python

import dataclasses
from collections.abc import Iterable

import click
from sqlalchemy import and_, or_

from inbox.config import config
from inbox.crispin import CrispinClient, writable_connection_pool
from inbox.models.account import Account
from inbox.models.backends.imap import ImapUid
from inbox.models.folder import Folder
from inbox.models.session import global_session_scope

config["USE_GEVENT"] = False


@dataclasses.dataclass
class LocalAccount:
    id: int
    email: str
    provider: str
    sync_state: str


def fetch_accounts(
    *, host: "str | None", account_id: "str | None"
) -> "list[LocalAccount]":
    with global_session_scope() as db_session:
        accounts = db_session.query(Account).filter(Account.sync_state == "running")
        if host:
            process_identifier = f"{host}:0"
            accounts = accounts.filter(
                Account.sync_should_run,
                or_(
                    and_(
                        Account.desired_sync_host == process_identifier,
                        Account.sync_host.is_(None),
                    ),
                    and_(
                        Account.desired_sync_host.is_(None),
                        Account.sync_host == process_identifier,
                    ),
                    and_(
                        Account.desired_sync_host == process_identifier,
                        Account.sync_host == process_identifier,
                    ),
                ),
            )
        if account_id:
            accounts = accounts.filter(Account.id == account_id)

        return [
            LocalAccount(
                id=account.id,
                email=account.email_address,
                provider=account.provider,
                sync_state=account.sync_state,
            )
            for account in accounts
        ]


@dataclasses.dataclass
class ServerInfo:
    welcome: str
    capabilities: list[str]


def get_server_info(crispin_client: CrispinClient, account: Account) -> ServerInfo:
    return ServerInfo(
        welcome=crispin_client.conn.welcome.decode(),
        capabilities=[
            capability.decode() for capability in crispin_client.conn.capabilities()
        ],
    )


@dataclasses.dataclass
class RemoteFolder:
    name: str
    role: "str | None"
    uidnext: int
    exists: int


def fetch_remote_folders(
    provider: str, crispin_client: CrispinClient
) -> Iterable[RemoteFolder]:
    try:
        folder_names = crispin_client.folder_names()
    except Exception:
        return

    for role, folders in folder_names.items():
        if provider == "gmail" and role not in ["all", "spam", "trash"]:
            continue

        for folder in folders:
            try:
                result = crispin_client.select_folder(
                    folder, lambda _account_id, _folder_name, select_info: select_info
                )
            except Exception:
                continue

            yield RemoteFolder(
                name=folder,
                role=role,
                uidnext=result[b"UIDNEXT"],
                exists=result[b"EXISTS"],
            )


@dataclasses.dataclass
class LocalFolder:
    id: int
    name: str
    exists: int
    state: str


def fetch_local_folders(account: LocalAccount) -> Iterable[LocalFolder]:
    with global_session_scope() as db_session:
        for folder in db_session.query(Folder).filter(Folder.account_id == account.id):
            exists = (
                db_session.query(ImapUid).filter(ImapUid.folder_id == folder.id).count()
            )
            yield LocalFolder(
                id=folder.id,
                name=folder.name,
                exists=exists,
                state=folder.imapsyncstatus.state,
            )


@click.command()
@click.option("--host", default=None)
@click.option("--account-id", default=None)
@click.option("--include-server-info", is_flag=True)
def main(host: "str | None", account_id: "str | None", include_server_info: bool):
    accounts = fetch_accounts(host=host, account_id=account_id)
    total_remote_exists = 0
    total_local_exists = 0
    for account in accounts:
        print(account)

        with writable_connection_pool(account.id).get() as crispin_client:
            if include_server_info:
                server_info = get_server_info(crispin_client, account)
                print("\t", server_info)
                print()

            total_folder_remote_exists = 0
            for remote_folder in fetch_remote_folders(account.provider, crispin_client):
                print("\t", remote_folder)
                total_folder_remote_exists += remote_folder.exists
                total_remote_exists += remote_folder.exists
            print("\t Total remote EXISTS:", total_folder_remote_exists)
            print()

            total_folder_local_exists = 0
            for local_folder in fetch_local_folders(account):
                print("\t", local_folder)
                total_folder_local_exists += local_folder.exists
                total_local_exists += local_folder.exists
            print("\t Total local EXISTS:", total_folder_local_exists)
            print()

    print("Total remote EXISTS:", total_remote_exists)
    print("Total local EXISTS:", total_local_exists)


if __name__ == "__main__":
    main()

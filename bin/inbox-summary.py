#!/usr/bin/env python

import dataclasses
from collections.abc import Iterable

import click
from sqlalchemy import and_, or_

from inbox.crispin import CrispinClient, writable_connection_pool
from inbox.models.account import Account
from inbox.models.backends.imap import ImapUid
from inbox.models.folder import Folder
from inbox.models.session import global_session_scope


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
        folders = crispin_client.folders()
    except Exception:
        return

    for folder in sorted(folders, key=lambda f: f.display_name):
        if provider == "gmail" and folder.role not in ["all", "spam", "trash"]:
            continue

        try:
            result = crispin_client.select_folder(
                folder.display_name,
                lambda _account_id, _folder_name, select_info: select_info,
            )
        except Exception:
            continue

        yield RemoteFolder(
            name=folder.display_name,
            role=folder.role,
            uidnext=result[b"UIDNEXT"],
            exists=result[b"EXISTS"],
        )


@dataclasses.dataclass
class LocalFolder:
    id: int
    name: str
    state: str
    uidmax: int
    exists: int


def fetch_local_folders(account: LocalAccount) -> Iterable[LocalFolder]:
    with global_session_scope() as db_session:
        for folder in (
            db_session.query(Folder)
            .filter(Folder.account_id == account.id)
            .order_by(Folder.name)
        ):
            exists = (
                db_session.query(ImapUid).filter(ImapUid.folder_id == folder.id).count()
            )
            uidmax = (
                db_session.query(ImapUid.msg_uid)
                .filter(ImapUid.folder_id == folder.id)
                .order_by(ImapUid.msg_uid.desc())
                .limit(1)
                .scalar()
            ) or 0
            yield LocalFolder(
                id=folder.id,
                name=folder.name,
                state=folder.imapsyncstatus.state,
                uidmax=uidmax,
                exists=exists,
            )


@dataclasses.dataclass
class SummarizedList:
    value: list
    max_values: int = 10

    def __repr__(self):
        if len(self.value) <= self.max_values:
            return repr(self.value)

        return f"[{self.value[0]}, ... ,{self.value[-1]} len={len(self.value)}]"


@dataclasses.dataclass
class LocalFolderDiff:
    name: str
    uids_to_add: list[int]
    uids_to_delete: list[int]


@dataclasses.dataclass
class LocalFolderMissing:
    name: str


def compare_local_and_remote(
    crispin_client: CrispinClient,
    remote_folders: list[RemoteFolder],
    local_folders: list[LocalFolder],
):
    remote_folders_by_name = {folder.name: folder for folder in remote_folders}
    local_folders_by_name = {folder.name: folder for folder in local_folders}

    for name, remote_folder in remote_folders_by_name.items():
        local_folder = local_folders_by_name.get(name)
        if not local_folder:
            yield LocalFolderMissing(name=name)

        if local_folder.exists == remote_folder.exists:
            continue

        crispin_client.select_folder(
            local_folder.name,
            lambda _account_id, _folder_name, select_info: select_info,
        )
        remote_uids = set(crispin_client.all_uids())
        with global_session_scope() as db_session:
            local_uids = set(
                uid
                for uid, in db_session.query(ImapUid.msg_uid).filter(
                    ImapUid.folder_id == local_folder.id
                )
            )

        uids_to_add = remote_uids - local_uids
        uids_to_delete = local_uids - remote_uids

        yield LocalFolderDiff(
            name=local_folder.name,
            uids_to_add=SummarizedList(sorted(uids_to_add)),
            uids_to_delete=SummarizedList(sorted(uids_to_delete)),
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

        try:
            with writable_connection_pool(account.id).get() as crispin_client:
                if include_server_info:
                    server_info = get_server_info(crispin_client, account)
                    print("\t", server_info)
                    print()

                total_folder_remote_exists = 0
                remote_folders = []
                for remote_folder in fetch_remote_folders(
                    account.provider, crispin_client
                ):
                    print("\t", remote_folder)
                    remote_folders.append(remote_folder)
                    total_folder_remote_exists += remote_folder.exists
                    total_remote_exists += remote_folder.exists
                print("\t Total remote EXISTS:", total_folder_remote_exists)
                print()

                total_folder_local_exists = 0
                local_folders = []
                for local_folder in fetch_local_folders(account):
                    print("\t", local_folder)
                    local_folders.append(local_folder)
                    total_folder_local_exists += local_folder.exists
                    total_local_exists += local_folder.exists
                print("\t Total local EXISTS:", total_folder_local_exists)
                print(
                    "\t Total difference:",
                    total_folder_remote_exists - total_folder_local_exists,
                )
                print()

                for diff in compare_local_and_remote(
                    crispin_client, remote_folders, local_folders
                ):
                    print("\t", diff)
                print()
        except Exception as e:
            print("\t Exception opening the connection", e)
            print()

    print("Total remote EXISTS:", total_remote_exists)
    print("Total local EXISTS:", total_local_exists)


if __name__ == "__main__":
    main()

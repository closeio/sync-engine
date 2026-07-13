#!/usr/bin/env python


from setproctitle import setproctitle  # type: ignore[import-not-found]

setproctitle("list-outlook-account-emails")

import csv
import os
import sys
from concurrent.futures import ThreadPoolExecutor

import click

from inbox.crispin import connection_pool
from inbox.events.microsoft.events_provider import MicrosoftEventsProvider
from inbox.models.backends.outlook import OutlookAccount
from inbox.models.session import global_session_scope


AccountInfo = tuple[int, int | None, str]
CsvRow = list[str]


def get_cluster() -> str:
    hostname = os.environ.get("HOSTNAME", "")
    return "-".join(hostname.split("-")[:2])


def get_imap_folder_names(account_id: int) -> str:
    try:
        cp = connection_pool(account_id)
        with cp.get() as crispin_client:
            folders = crispin_client.folders()

        return ",".join(
            sorted(folder.display_name for folder in folders)
        )
    except Exception:
        return ""


def get_graph_folder_names(
    account_id: int, namespace_id: int | None
) -> str:
    if namespace_id is None:
        return ""

    try:
        events_provider = MicrosoftEventsProvider(account_id, namespace_id)
        return ",".join(
            sorted(
                folder["displayName"]
                for folder in events_provider.client._iter(
                    "/me/mailFolders"
                )
            )
        )
    except Exception:
        return ""


def process_account(cluster: str, account_info: AccountInfo) -> CsvRow:
    account_id, namespace_id, email_address = account_info
    return [
        cluster,
        email_address,
        get_imap_folder_names(account_id),
        get_graph_folder_names(account_id, namespace_id),
    ]


@click.command()
@click.option(
    "--concurrency",
    type=int,
    default=5,
    show_default=True,
    help="Number of accounts to process in parallel.",
)
def main(concurrency: int) -> None:
    """Print the email address and remote folder names for every Outlook account."""
    if concurrency < 1:
        raise click.BadParameter(
            "must be at least 1", param_hint="--concurrency"
        )

    cluster = get_cluster()
    writer = csv.writer(sys.stdout)
    writer.writerow(
        [
            "cluster",
            "email_address",
            "imap_folder_names",
            "graph_folder_names",
        ]
    )

    with global_session_scope() as db_session:
        accounts = db_session.query(OutlookAccount).filter(
            OutlookAccount.deleted_at.is_(None)
        )
        account_infos: list[AccountInfo] = [
            (
                account.id,
                account.namespace.id if account.namespace else None,
                account.email_address or "",
            )
            for account in accounts.yield_per(100)
        ]

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [
            executor.submit(process_account, cluster, account_info)
            for account_info in account_infos
        ]
        for future in futures:
            writer.writerow(future.result())


if __name__ == "__main__":
    main()

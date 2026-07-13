#!/usr/bin/env python


from setproctitle import setproctitle  # type: ignore[import-not-found]

setproctitle("list-outlook-account-emails")

import csv
import os
import sys

import click

from inbox.crispin import connection_pool
from inbox.events.microsoft.events_provider import MicrosoftEventsProvider
from inbox.models.backends.outlook import OutlookAccount
from inbox.models.session import global_session_scope


def get_cluster() -> str:
    hostname = os.environ.get("HOSTNAME", "")
    return hostname.split("-mgmt-", 1)[0]


@click.command()
def main() -> None:
    """Print the email address and remote folder names for every Outlook account."""
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
        for account in db_session.query(OutlookAccount).yield_per(100):
            try:
                cp = connection_pool(account.id)
                with cp.get() as crispin_client:
                    folders = crispin_client.folders()

                imap_folder_names = ", ".join(
                    sorted(folder.display_name for folder in folders)
                )
            except Exception:
                imap_folder_names = ""

            try:
                events_provider = MicrosoftEventsProvider(
                    account.id, account.namespace.id
                )
                graph_folder_names = ", ".join(
                    sorted(
                        folder["displayName"]
                        for folder in events_provider.client._iter(
                            "/me/mailFolders"
                        )
                    )
                )
            except Exception:
                graph_folder_names = ""

            writer.writerow(
                [
                    cluster,
                    account.email_address,
                    imap_folder_names,
                    graph_folder_names,
                ]
            )


if __name__ == "__main__":
    main()

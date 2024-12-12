#!/usr/bin/env python  # noqa: N999
import argparse
import sys

from inbox.error_handling import maybe_enable_rollbar
from inbox.util.db import drop_everything


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-u",
        "--with-users",
        action="store_true",
        dest="with_users",
        default=False,
    )
    args = parser.parse_args()
    from inbox.ignition import init_db, main_engine

    maybe_enable_rollbar()

    engine = main_engine(pool_size=1)

    # Always keep the 'alembic_version' table
    keep_tables = ["alembic_version"]
    reset_columns = {}

    # '--with-users' NOT specified
    if not args.with_users:
        keep_tables += [
            "user",
            "namespace",
            "account",
            "imapaccount",
            "user_session",
            "easaccount",
            "folder",
            "gmailaccount",
            "outlookaccount",
            "genericaccount",
            "secret",
            "calendar",
            "easdevice",
        ]

        reset_columns = {"easaccount": ["eas_account_sync_key", "eas_state"]}

    drop_everything(
        engine, keep_tables=keep_tables, reset_columns=reset_columns
    )
    # recreate dropped tables
    init_db(engine)
    sys.exit(0)


if __name__ == "__main__":
    main()

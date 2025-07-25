#!/usr/bin/env python
# Query the id corresponding to a public id and vice-versa.


import sys

import click
import IPython

from inbox.error_handling import maybe_enable_error_reporting
from inbox.models import (
    Account,
    Block,
    Calendar,
    Event,
    Message,
    Namespace,
    Part,
    Thread,
    Transaction,
)
from inbox.models.session import global_session_scope

cls_for_type = dict(
    account=Account,
    message=Message,
    namespace=Namespace,
    block=Block,
    part=Part,
    thread=Thread,
    event=Event,
    calendar=Calendar,
    transaction=Transaction,
)

try:
    from inbox.models.backends.eas import (  # type: ignore[import-not-found]
        EASFolderSyncStatus,
    )

    cls_for_type["easfoldersyncstatus"] = EASFolderSyncStatus
except ImportError:
    pass


@click.command()
@click.option("--type", "-t", type=str, required=True)
@click.option("--id", type=str, default=None)
@click.option("--public-id", type=str, default=None)
@click.option("--account-id", type=str, default=None)
@click.option("--namespace-id", type=str, default=None)
@click.option("--readwrite", is_flag=True, default=False)
def main(  # type: ignore[no-untyped-def]
    type, id, public_id, account_id, namespace_id, readwrite
) -> None:
    maybe_enable_error_reporting()

    type = type.lower()  # noqa: A001

    if type not in cls_for_type:
        print(f"Error: unknown type '{type}'")
        sys.exit(-1)

    cls = cls_for_type[type]

    if all([id, public_id, account_id, namespace_id]):
        print("Error: you should specify an id or public id to query.")
        sys.exit(-1)

    with global_session_scope() as db_session, db_session.no_autoflush:
        qu = db_session.query(cls)

        if public_id:
            qu = qu.filter(
                cls.public_id == public_id  # type: ignore[attr-defined]
            )
        elif id:
            qu = qu.filter(cls.id == id)  # type: ignore[attr-defined]

        if account_id:
            qu = qu.filter(
                cls.account_id == account_id  # type: ignore[attr-defined]
            )
        elif namespace_id:
            qu = qu.filter(
                cls.namespace_id == namespace_id  # type: ignore[attr-defined]
            )

        obj = qu.one()  # noqa: F841

        banner = """The object you queried is accessible as `obj`.
Note that the db session is read-only, unless if you start this script with --readwrite"""
        IPython.embed(banner1=banner)

        if readwrite is False:
            print("Rolling-back db session.")
            db_session.rollback()


if __name__ == "__main__":
    main()

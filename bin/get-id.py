#!/usr/bin/env python
# Query the id corresponding to a public id and vice-versa.


import sys

import click

from inbox.error_handling import maybe_enable_rollbar
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
    block=Block,
    part=Part,
    namespace=Namespace,
    thread=Thread,
    event=Event,
    calendar=Calendar,
    transaction=Transaction,
)


@click.command()
@click.option("--type", "-t", type=str, required=True)
@click.option("--id", type=str, default=None)
@click.option("--public-id", type=str, default=None)
def main(type, id, public_id) -> None:  # type: ignore[no-untyped-def]
    maybe_enable_rollbar()

    type = type.lower()  # noqa: A001

    if type not in cls_for_type:
        print(f"Error: unknown type '{type}'")
        sys.exit(-1)

    cls = cls_for_type[type]

    if public_id is None and id is None:
        print("Error: you should specify an id or public id to query.")
        sys.exit(-1)

    with global_session_scope() as db_session:
        if public_id:
            obj = (
                db_session.query(cls)
                .filter(
                    cls.public_id == public_id  # type: ignore[attr-defined]
                )
                .one()
            )
            print(obj.id)
        elif id:
            obj = (
                db_session.query(cls)
                .filter(cls.id == id)  # type: ignore[attr-defined]
                .one()
            )
            print(obj.public_id)


if __name__ == "__main__":
    main()

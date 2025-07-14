#!/usr/bin/env python
"""
Create event contact associations for events that don't have any.
"""


import click
from sqlalchemy import asc  # type: ignore[import-untyped]

from inbox.contacts.processing import update_contacts_from_event
from inbox.error_handling import maybe_enable_error_reporting
from inbox.ignition import engine_manager
from inbox.logging import configure_logging, get_logger
from inbox.models import Event
from inbox.models.session import session_scope_by_shard_id
from inbox.models.util import limitlion

configure_logging()
log = get_logger(purpose="create-event-contact-associations")


def process_shard(  # type: ignore[no-untyped-def]
    shard_id, dry_run, id_start: int = 0
) -> None:
    # At 500K events, we need to process 6 events per second to finish within a day.
    batch_size = 100
    rps = 6 / batch_size
    window = 5

    throttle = limitlion.throttle_wait(
        "create-event-contact-associations", rps=rps, window=window
    )

    with session_scope_by_shard_id(shard_id) as db_session:
        # NOTE: The session is implicitly autoflushed, which ensures no
        # duplicate contacts are created.

        n = 0
        n_skipped = 0
        n_updated = 0

        while True:
            event_query = list(
                db_session.query(Event)
                .filter(Event.id > id_start)
                .order_by(asc(Event.id))
                .limit(batch_size)
            )

            if not event_query:
                break

            for event in event_query:
                n += 1
                id_start = event.id

                if n % batch_size == 0:
                    log.info(
                        "progress",
                        shard_id=shard_id,
                        id_start=id_start,
                        n=n,
                        n_skipped=n_skipped,
                        n_updated=n_updated,
                    )

                if event.contacts:
                    continue

                if not dry_run:
                    update_contacts_from_event(
                        db_session, event, event.namespace_id
                    )
                    n_updated += 1

                    if n_updated % batch_size == 0:
                        db_session.commit()
                        log.info(
                            "committed",
                            shard_id=shard_id,
                            n=n,
                            n_skipped=n_skipped,
                            n_updated=n_updated,
                        )
                        throttle()

    log.info(
        "finished",
        shard_id=shard_id,
        n=n,
        n_skipped=n_skipped,
        n_updated=n_updated,
    )


@click.command()
@click.option("--shard-id", type=int, default=None)
@click.option("--id-start", type=int, default=0)
@click.option("--dry-run", is_flag=True)
def main(shard_id, id_start, dry_run) -> None:  # type: ignore[no-untyped-def]
    maybe_enable_error_reporting()

    if shard_id is not None:
        process_shard(shard_id, dry_run, id_start)
    else:
        for shard_id in engine_manager.engines:
            process_shard(shard_id, dry_run, id_start)


if __name__ == "__main__":
    main()

#!/usr/bin/env python  # noqa: N999

import click
from sqlalchemy import func

from inbox.error_handling import maybe_enable_rollbar
from inbox.ignition import engine_manager
from inbox.models import Account, Namespace
from inbox.models.action_log import ActionLog
from inbox.models.session import session_scope_by_shard_id


@click.command()
def main() -> None:
    """
    Generate per-shard and per-namespace breakdowns of syncback queue
    lengths.

    """
    maybe_enable_rollbar()

    for key in engine_manager.engines:
        with session_scope_by_shard_id(key) as db_session:
            total_pending_actions = 0
            for c, namespace_id in (
                db_session.query(
                    func.count(ActionLog.namespace_id), ActionLog.namespace_id
                )
                .join(Namespace)
                .join(Account)
                .filter(
                    ActionLog.discriminator == "actionlog",
                    Account.sync_state != "invalid",
                    ActionLog.status == "pending",
                )
                .group_by(ActionLog.namespace_id)
            ):
                print(
                    f"{c} (pending actions), {key} (shard), {namespace_id} (namespace)"
                )
                total_pending_actions += c
            print(
                f"total pending actions for shard {key}: {total_pending_actions}"
            )


if __name__ == "__main__":
    main()

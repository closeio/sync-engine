#!/usr/bin/env python

import IPython

from inbox.models import (
    Account,
    ActionLog,
    Block,
    Calendar,
    Category,
    Event,
    Folder,
    Label,
    Message,
    Namespace,
    Part,
    Thread,
    Transaction,
)
from inbox.models.session import global_session_scope


def main():
    with global_session_scope() as db_session, db_session.no_autoflush:
        IPython.embed()


if __name__ == "__main__":
    main()

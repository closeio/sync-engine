from sqlalchemy.engine import reflection  # type: ignore[import-untyped]
from sqlalchemy.schema import (  # type: ignore[import-untyped]
    DropConstraint,
    DropTable,
    ForeignKeyConstraint,
    MetaData,
    Table,
)


# http://www.sqlalchemy.org/trac/wiki/UsageRecipes/DropEverything
def drop_everything(  # type: ignore[no-untyped-def]
    engine, keep_tables=None, reset_columns=None
) -> None:
    """
    Drops all tables in the db unless their name is in `keep_tables`.
    `reset_columns` is used to specify the columns that should be reset to
    default value in the tables that we're keeping -
    provided as a dict of table_name: list_of_column_names.
    """  # noqa: D401
    keep_tables = keep_tables or []
    reset_columns = reset_columns or {}
    conn = engine.connect()
    trans = conn.begin()

    inspector = reflection.Inspector.from_engine(engine)

    # gather all data first before dropping anything.
    # some DBs lock after things have been dropped in
    # a transaction.

    metadata = MetaData()

    tbs = []
    all_fks = []

    for table_name in inspector.get_table_names():
        if table_name in keep_tables:
            # Reset certain columns in certain tables we're keeping
            if table_name in reset_columns:
                t = Table(table_name, metadata)

                column_names = reset_columns[table_name]
                for c in inspector.get_columns(table_name):
                    if c["name"] in column_names:
                        assert c["default"]

                        q = "UPDATE {} SET {}={};".format(  # noqa: S608
                            table_name, c["name"], c["default"]
                        )
                        conn.execute(q)
            continue

        fks = []
        for fk in inspector.get_foreign_keys(table_name):
            if not fk["name"]:
                continue
            fks.append(ForeignKeyConstraint((), (), name=fk["name"]))
        t = Table(table_name, metadata, *fks)
        tbs.append(t)
        all_fks.extend(fks)

    for fkc in all_fks:
        conn.execute(DropConstraint(fkc))

    for table in tbs:
        conn.execute(DropTable(table))

    trans.commit()

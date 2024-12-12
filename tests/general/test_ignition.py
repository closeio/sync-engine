import pytest

from inbox.ignition import init_db, reset_invalid_autoincrements, verify_db
from inbox.util.sharding import get_shard_schemas
from inbox.util.testutils import create_test_db, setup_test_db


@pytest.fixture(scope="function")
def base_db(config):
    from inbox.ignition import engine_manager

    create_test_db()
    yield engine_manager
    setup_test_db()


def test_verify_db(base_db):
    engines = base_db.engines
    shard_schemas = get_shard_schemas()

    # A correctly set auto_increment.
    key = 0
    init_db(engines[key], key)
    verify_db(engines[key], shard_schemas[key], key)

    # An incorrectly set auto_increment.
    key = 1
    init_db(engines[key], key + 1)
    with pytest.raises(AssertionError):
        verify_db(engines[key], shard_schemas[key], key)


def test_reset_autoincrements(base_db):
    engines = base_db.engines
    shard_schemas = get_shard_schemas()

    # A correctly set auto_increment.
    key = 0
    init_db(engines[key], key)
    reset_tables = reset_invalid_autoincrements(
        engines[key], shard_schemas[key], key, False
    )
    assert len(reset_tables) == 0

    # Ensure dry_run mode does not reset tables
    key = 1
    init_db(engines[key], key + 1)
    reset_tables = reset_invalid_autoincrements(
        engines[key], shard_schemas[key], key, True
    )
    assert len(reset_tables) > 0

    with pytest.raises(AssertionError):
        verify_db(engines[key], shard_schemas[key], key)

    reset_tables = reset_invalid_autoincrements(
        engines[key], shard_schemas[key], key, False
    )

    assert len(reset_tables) > 0
    verify_db(engines[key], shard_schemas[key], key)

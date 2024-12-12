import json
import time
from datetime import datetime, timedelta

import pytest

import inbox.heartbeat.config as heartbeat_config
from inbox.config import config
from inbox.heartbeat.config import ALIVE_EXPIRY
from inbox.heartbeat.status import clear_heartbeat_status, get_ping_status
from inbox.heartbeat.store import (
    HeartbeatStatusKey,
    HeartbeatStatusProxy,
    HeartbeatStore,
)
from inbox.logging import configure_logging

configure_logging(config.get("LOGLEVEL"))


# Note that all Redis commands are mocked via mockredis in conftest.py.


def proxy_for(
    account_id, folder_id, email="test@test.com", provider="gmail", device_id=0
):
    return HeartbeatStatusProxy(
        account_id=account_id,
        folder_id=folder_id,
        folder_name="Inbox",
        email_address=email,
        provider_name=provider,
        device_id=device_id,
    )


def fuzzy_equals(a, b):
    if isinstance(a, datetime) or isinstance(b, datetime):
        if not isinstance(a, datetime):
            b = datetime.fromtimestamp(a)
        if not isinstance(b, datetime):
            b = datetime.fromtimestamp(b)
        s = abs(a - b)
        return s < timedelta(seconds=0.1)
    return abs(a - b) < 0.1


# Test storing and removing heartbeats


def test_heartbeat_store_singleton():
    # Test we don't unnecessarily create multiple instances of HeartbeatStore
    store_one = HeartbeatStore.store()
    store_two = HeartbeatStore.store()
    assert id(store_one) == id(store_two)


def test_heartbeat_status_key():
    account_id = 1
    folder_id = 2
    key = HeartbeatStatusKey(account_id, folder_id)
    assert str(key) == "1:2"
    key = HeartbeatStatusKey.from_string("2:1")
    assert key.account_id == 2
    assert key.folder_id == 1


def test_proxy_publish_doesnt_break_everything(monkeypatch):
    def break_things(s, k, d, v):
        raise Exception("Redis connection failure")

    monkeypatch.setattr("mockredis.MockRedis.hset", break_things)
    # Check heartbeat publish exception doesn't pass up through to caller.
    # It will print out an error in the log, though.
    proxy_for(1, 2)
    assert True


def test_folder_publish_in_index(redis_client):
    proxy = proxy_for(1, 2)
    proxy.publish()
    client = heartbeat_config.get_redis_client()
    assert "1" in [key.decode() for key in client.keys()]

    # Check the per-account folder-list index was populated correctly: it
    # should be a sorted set of all folder IDs for that account, with the
    # folder's last heartbeat timestamp.
    acct_folder_index = client.zrange("1", 0, -1, withscores=True)
    assert len(acct_folder_index) == 1
    key, timestamp = acct_folder_index[0]
    assert key.decode() == "2"
    assert fuzzy_equals(proxy.heartbeat_at, timestamp)


def test_kill_device_multiple():
    # If we kill a device and the folder has multiple devices, don't clear
    # the heartbeat status
    local_store = HeartbeatStore().store()

    proxy_for(1, 2, device_id=2).publish()
    proxy_for(1, 2, device_id=3).publish()
    clear_heartbeat_status(1, device_id=2)
    folders = local_store.get_account_folders(1)

    assert len(folders) == 1
    f, ts = folders[0]
    assert f.decode() == "2"


# Test querying heartbeats
@pytest.fixture
def random_heartbeats():
    # generate some random heartbeats for accounts 1..10 and folders -2..2
    proxies = {}
    for i in range(10):
        proxies[i] = {}
        for f in range(-2, 3):
            proxy = proxy_for(i, f)
            proxy.publish()
            proxies[i][f] = proxy
    return proxies


def make_dead_heartbeat(store, proxies, account_id, folder_id, time_dead):
    dead_time = time.time() - ALIVE_EXPIRY - time_dead
    dead_proxy = proxies[account_id][folder_id]
    store.publish(
        dead_proxy.key,
        dead_proxy.device_id,
        json.dumps(dead_proxy.value),
        dead_time,
    )


def test_ping(random_heartbeats):
    # Get the lightweight ping (only checks indices) and make sure it conforms
    # to the expected format.
    ping = get_ping_status(list(range(10)))
    assert isinstance(ping, dict)
    assert sorted(ping.keys()) == sorted(random_heartbeats.keys())
    single = ping[0]
    attrs = ("id", "folders")
    for attr in attrs:
        assert hasattr(single, attr)
    for f in single.folders:
        assert f.alive


def test_ping_single(random_heartbeats):
    ping = get_ping_status([0])
    assert isinstance(ping, dict)
    single = ping[0]
    for f in single.folders:
        assert f.alive

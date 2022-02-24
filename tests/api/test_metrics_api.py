import json

import pytest

from inbox.ignition import redis_txn

from tests.util.base import add_fake_message


class TestGlobalDeltas:
    @pytest.fixture(autouse=True)
    def clear_redis(self):
        redis_txn.flushdb()

    @pytest.fixture
    def unauthed_api_client(self, db, default_namespace):
        from inbox.api.srv import app

        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_global_deltas(self, db, unauthed_api_client, default_namespace, thread):
        deltas_base_url = "/metrics/global-deltas/"

        # add a fake message
        add_fake_message(
            db.session,
            default_namespace.id,
            thread,
            from_addr=[("Bob", "bob@foocorp.com")],
        )

        # pull for global deltas. the default namespace should be returned
        response = unauthed_api_client.get(deltas_base_url)
        deltas = json.loads(response.data)
        assert str(default_namespace.public_id) in deltas["deltas"]
        txnid = deltas["txnid_end"]

        # pull again, but with a cursor this time. nothing should be returned
        response = unauthed_api_client.get(f"/metrics/global-deltas?txnid={txnid}")
        deltas = json.loads(response.data)
        assert not deltas["deltas"]
        assert txnid == deltas["txnid_end"]

        # add another fake message
        add_fake_message(
            db.session,
            default_namespace.id,
            thread,
            from_addr=[("Bob", "bob@foocorp.com")],
        )

        # pull for global deltas again with a txnid
        response = unauthed_api_client.get(f"/metrics/global-deltas?txnid={txnid}")
        deltas = json.loads(response.data)

        # the default namespace should be returned again
        assert str(default_namespace.public_id) in deltas["deltas"]
        assert deltas["txnid_end"] > txnid

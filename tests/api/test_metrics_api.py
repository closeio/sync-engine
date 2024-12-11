import json
from unittest import mock

import pytest

from inbox.ignition import redis_txn
from inbox.models.namespace import Namespace
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

    def test_global_deltas(
        self, db, unauthed_api_client, default_namespace, thread
    ):
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
        response = unauthed_api_client.get(
            f"/metrics/global-deltas?txnid={txnid}"
        )
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
        response = unauthed_api_client.get(
            f"/metrics/global-deltas?txnid={txnid}"
        )
        deltas = json.loads(response.data)

        # the default namespace should be returned again
        assert str(default_namespace.public_id) in deltas["deltas"]
        assert deltas["txnid_end"] > txnid


def test_metrics_index(test_client, outlook_account):
    metrics = test_client.get("/metrics")

    (outlook_account_metrics,) = metrics.json
    assert outlook_account_metrics["account_private_id"] == outlook_account.id
    assert (
        outlook_account_metrics["namespace_private_id"]
        == outlook_account.namespace.id
    )


def test_metrics_index_busted_account(
    db, test_client, outlook_account, default_account
):
    # Bust outlook_account by deleting its namespace
    db.session.query(Namespace).filter_by(
        id=outlook_account.namespace.id
    ).delete()
    db.session.commit()

    with mock.patch("inbox.api.metrics_api.log") as log_mock:
        metrics = test_client.get("/metrics")

    # outlook_account gets error
    ((method, (message,), kwargs),) = log_mock.method_calls
    assert method == "error"
    assert message == "Error while serializing account metrics"
    assert kwargs["account_id"] == outlook_account.id

    # but we still serialize default_account
    (default_account_metrics,) = metrics.json
    assert default_account_metrics["account_private_id"] == default_account.id
    assert (
        default_account_metrics["namespace_private_id"]
        == default_account.namespace.id
    )

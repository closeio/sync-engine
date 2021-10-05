from pytest import fixture

from tests.api.base import new_api_client


@fixture
def imap_api_client(db, generic_account):
    return new_api_client(db, generic_account.namespace)

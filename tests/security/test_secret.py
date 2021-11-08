# -*- coding: UTF-8 -*-
import pytest

from inbox.auth.google import GoogleAccountData, GoogleAuthHandler
from inbox.models.secret import Secret, SecretType

SHARD_ID = 0
ACCOUNT_ID = 1


@pytest.mark.parametrize("encrypt", [True, False])
def test_secret(db, config, encrypt):
    """
    If encryption is enabled, ensure that:
    * secrets are encrypted.
    * secrets are decrypted correctly on retrieval.
    * secrets are bytes.
    """
    config["ENCRYPT_SECRETS"] = encrypt
    bytes_secret = b"\xff\x00\xf1"
    unicode_secret = u"foo\u00a0"

    secret = Secret()
    secret.type = "password"
    secret.secret = bytes_secret

    db.session.add(secret)
    db.session.commit()

    secret = db.session.query(Secret).get(secret.id)

    if encrypt:
        assert secret._secret != bytes_secret, "secret is not encrypted"
    else:
        assert secret._secret == bytes_secret
    assert secret.secret == bytes_secret, "secret not decrypted correctly"

    secret.secret = unicode_secret
    assert secret.secret == unicode_secret.encode("utf8")


@pytest.mark.parametrize("encrypt", [True, False])
def test_token(db, config, encrypt):
    """
    If encryption is enabled, ensure that:
    * tokens are encrypted.
    * tokens are decrypted correctly on retrieval.

    Note: This tests refresh_tokens but passwords work in the same way

    """
    config["ENCRYPT_SECRETS"] = encrypt
    token = u"tH*$&123abcº™™∞"

    email = "vault.test@localhost.com"
    account_data = GoogleAccountData(
        email=email,
        secret_type=SecretType.Token,
        secret_value=token,
        client_id="",
        scope="a b",
        sync_email=True,
        sync_contacts=False,
        sync_events=True,
    )
    g = GoogleAuthHandler()
    g.verify_config = lambda x: True
    account = g.create_account(account_data)

    db.session.add(account)
    db.session.commit()

    secret_id = account.refresh_token_id
    secret = db.session.query(Secret).get(secret_id)

    assert secret == account.secret

    if encrypt:
        assert secret._secret != token, "token not encrypted"
    else:
        assert secret._secret == token.encode(
            "utf-8"
        ), "token encrypted when encryption disabled"

    decrypted_secret = secret.secret  # type: bytes
    assert decrypted_secret == token.encode(
        "utf-8"
    ) and account.refresh_token == decrypted_secret.decode(
        "utf-8"
    ), "token not decrypted correctly"

    # db.session.delete(account.auth_credentials[0])
    db.session.commit()


@pytest.mark.parametrize("encrypt", [True, False])
def test_token_inputs(db, config, encrypt, default_account):
    """
    Ensure unicode tokens are converted to bytes.
    Ensure invalid UTF-8 tokens are handled correctly.

    """
    config["ENCRYPT_SECRETS"] = encrypt
    # Unicode
    unicode_token = u"myunicodesecret"

    # Invalid UTF-8 byte sequence
    invalid_token = b"\xff\x10"

    # NULL byte
    null_token = b"\x1f\x00\xf1"

    default_account.refresh_token = unicode_token
    db.session.commit()

    secret_id = default_account.refresh_token_id
    secret = db.session.query(Secret).get(secret_id)

    assert isinstance(secret.secret, bytes), "secret cannot be unicode"
    assert secret.secret == unicode_token.encode(
        "utf-8"
    ), "token not decrypted correctly"

    with pytest.raises(ValueError):
        default_account.refresh_token = invalid_token

    with pytest.raises(ValueError):
        default_account.refresh_token = null_token

    assert default_account.refresh_token == unicode_token

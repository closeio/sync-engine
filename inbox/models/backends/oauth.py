"""
Generic OAuth class that provides abstraction for access and
refresh tokens.
"""
from datetime import datetime, timedelta

from sqlalchemy import Column, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declared_attr

from inbox.models.secret import Secret
from nylas.logging import get_logger

log = get_logger()


class TokenManager(object):
    def __init__(self):
        self._tokens = {}

    def get_token(self, account, force_refresh=False):
        if account.id in self._tokens:
            token, expiration = self._tokens[account.id]
            if not force_refresh and expiration > datetime.utcnow():
                return token

        new_token, expires_in = account.new_token()
        self.cache_token(account, new_token, expires_in)
        return new_token

    def cache_token(self, account, token, expires_in):
        expires_in -= 10
        expiration = datetime.utcnow() + timedelta(seconds=expires_in)
        self._tokens[account.id] = token, expiration


token_manager = TokenManager()


class OAuthAccount(object):
    # Secret

    @declared_attr
    def refresh_token_id(cls):
        return Column(ForeignKey(Secret.id), nullable=False)

    @declared_attr
    def secret(cls):
        return relationship("Secret", cascade="all", uselist=False, lazy="joined")

    @property
    def refresh_token(self):
        if not self.secret:
            return None
        return self.secret.secret

    @refresh_token.setter
    def refresh_token(self, value):
        # Must be a valid UTF-8 byte sequence without NULL bytes.
        if isinstance(value, unicode):
            value = value.encode("utf-8")

        try:
            unicode(value, "utf-8")
        except UnicodeDecodeError:
            raise ValueError("Invalid refresh_token")

        if b"\x00" in value:
            raise ValueError("Invalid refresh_token")

        if not self.secret:
            self.secret = Secret()

        self.secret.secret = value
        self.secret.type = "token"

    def new_token(self):
        try:
            return self.auth_handler.new_token(
                self.refresh_token, self.client_id, self.client_secret
            )
        except Exception as e:
            log.error(
                "Error while getting access token: {}".format(e),
                account_id=self.id,
                exc_info=True,
            )
            raise

    def verify(self):
        # TODO(emfree): update callers and remove this silliness.
        token = token_manager.get_token(self)
        return self.auth_handler.validate_token(token)

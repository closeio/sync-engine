"""
Generic OAuth class that provides abstraction for access and
refresh tokens.
"""
from datetime import datetime, timedelta
from hashlib import sha256
from typing import Union

from sqlalchemy import Column, ForeignKey
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import relationship

from inbox.basicauth import OAuthError
from inbox.logging import get_logger
from inbox.models.secret import Secret, SecretType

log = get_logger()


def hash_token(token, prefix=None):
    if not token:
        return None
    string = f"{prefix}:{token}" if prefix else token
    return sha256(string.encode()).hexdigest()


def log_token_usage(reason, refresh_token=None, access_token=None, account=None):
    nylas_account_id = (
        account.namespace.public_id if account and account.namespace else None
    )
    log.info(
        reason,
        refresh_hash=hash_token(refresh_token, prefix="refresh_token"),
        access_hash=hash_token(access_token, prefix="access_token"),
        nylas_account_id=nylas_account_id,
        email=account.email_address if account else None,
    )


class TokenManager:
    def __init__(self):
        self._tokens = {}

    def get_token(self, account, force_refresh=False):
        if account.id in self._tokens:
            token, expiration = self._tokens[account.id]
            if not force_refresh and expiration > datetime.utcnow():
                log_token_usage(
                    "access token used", access_token=token, account=account
                )
                return token

        new_token, expires_in = account.new_token(force_refresh=force_refresh)
        log_token_usage(
            "access token obtained", access_token=new_token, account=account
        )
        log_token_usage("access token used", access_token=new_token, account=account)
        self.cache_token(account, new_token, expires_in)
        return new_token

    def cache_token(self, account, token, expires_in):
        expires_in -= 10
        expiration = datetime.utcnow() + timedelta(seconds=expires_in)
        self._tokens[account.id] = token, expiration


token_manager = TokenManager()


class OAuthAccount:
    @declared_attr
    def refresh_token_id(cls):
        return Column(ForeignKey(Secret.id), nullable=False)

    @declared_attr
    def secret(cls):
        return relationship(
            "Secret",
            cascade="all",
            uselist=False,
            lazy="joined",
            foreign_keys=[cls.refresh_token_id],
        )

    @property
    def refresh_token(self):
        # type: () -> str
        if not self.secret:
            return None
        if self.secret.type == SecretType.Token.value:
            secret_value = self.secret.secret.decode("utf-8")
            log_token_usage(
                "refresh token used", refresh_token=secret_value, account=self
            )
            return secret_value
        else:
            raise ValueError("Invalid secret type.")

    @refresh_token.setter
    def refresh_token(self, value):
        # type: (Union[str, bytes]) -> None
        # Must be a valid UTF-8 byte sequence without NULL bytes.
        if not isinstance(value, bytes):
            value = value.encode("utf-8")

        try:
            value.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Invalid refresh_token")

        if b"\x00" in value:
            raise ValueError("Invalid refresh_token")

        log_token_usage("refresh token stored", refresh_token=value, account=self)
        self.set_secret(SecretType.Token, value)

    def set_secret(self, secret_type, secret_value):
        # type: (SecretType, bytes) -> None
        if not self.secret:
            self.secret = Secret()

        self.secret.type = secret_type.value
        self.secret.secret = secret_value

    def get_client_info(self):
        """
        Obtain the client ID and secret for this OAuth account.

        Return:
            Tuple with (client_id, client_secret).
        """
        if not self.client_id or self.client_id == self.OAUTH_CLIENT_ID:
            return (self.OAUTH_CLIENT_ID, self.OAUTH_CLIENT_SECRET)
        else:
            raise OAuthError("No valid tokens.")

    def new_token(self, force_refresh=False):
        """
        Retrieves a new access token.

        Args:
            force_refresh (bool): Whether a token refresh should be forced when
                requesting it from an external token service (AuthAlligator)
        Returns:
            A tuple with the new access token and its expiration.

        Raises:
            OAuthError: If no token could be obtained.
        """
        try:
            return self.auth_handler.acquire_access_token(
                self, force_refresh=force_refresh
            )
        except Exception as e:
            log.error(
                f"Error while getting access token: {e}",
                force_refresh=force_refresh,
                account_id=self.id,
                exc_info=True,
            )
            raise

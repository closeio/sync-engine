"""
Generic OAuth class that provides abstraction for access and
refresh tokens.
"""

from datetime import datetime, timedelta
from hashlib import sha256

from sqlalchemy import Column, ForeignKey  # type: ignore[import-untyped]
from sqlalchemy.ext.declarative import (  # type: ignore[import-untyped]
    declared_attr,
)
from sqlalchemy.orm import relationship  # type: ignore[import-untyped]

from inbox.exceptions import OAuthError
from inbox.logging import get_logger
from inbox.models.secret import Secret, SecretType

log = get_logger()


def hash_token(token, prefix=None):  # type: ignore[no-untyped-def]  # noqa: ANN201
    if not token:
        return None
    string = f"{prefix}:{token}" if prefix else token
    return sha256(string.encode()).hexdigest()


def log_token_usage(  # type: ignore[no-untyped-def]
    reason, refresh_token=None, access_token=None, account=None, scopes=None
) -> None:
    nylas_account_id = (
        account.namespace.public_id if account and account.namespace else None
    )
    log.info(
        reason,
        refresh_hash=hash_token(refresh_token, prefix="refresh_token"),
        access_hash=hash_token(access_token, prefix="access_token"),
        nylas_account_id=nylas_account_id,
        email=(account.email_address if account else None),
        scopes=scopes,
    )


class TokenManager:
    def __init__(self) -> None:
        self._token_cache: dict[
            tuple[str, tuple[str, ...] | None], tuple[str, datetime]
        ] = {}

    def get_token(
        self,
        account: "OAuthAccount",
        force_refresh: bool = False,
        scopes: list[str] | None = None,
    ) -> str:
        cache_key = self.get_cache_key(account, scopes)
        if cache_key in self._token_cache:
            token, expiration = self._token_cache[cache_key]
            if not force_refresh and expiration > datetime.utcnow():
                log_token_usage(
                    "access token used",
                    access_token=token,
                    account=account,
                    scopes=scopes,
                )
                return token

        new_token, expires_in = account.new_token(
            force_refresh=force_refresh, scopes=scopes
        )
        log_token_usage(
            "access token obtained",
            access_token=new_token,
            account=account,
            scopes=scopes,
        )
        log_token_usage(
            "access token used",
            access_token=new_token,
            account=account,
            scopes=scopes,
        )
        self.cache_token(account, scopes, new_token, expires_in)
        return new_token

    def get_cache_key(
        self, account: "OAuthAccount", scopes: list[str] | None
    ) -> tuple[str, tuple[str, ...] | None]:
        return (
            account.id,  # type: ignore[attr-defined]
            (tuple(scopes) if scopes else None),
        )

    def cache_token(
        self,
        account: "OAuthAccount",
        scopes: list[str] | None,
        token: str,
        expires_in: int,
    ) -> None:
        expires_in -= 10
        expiration = datetime.utcnow() + timedelta(seconds=expires_in)
        cache_key = self.get_cache_key(account, scopes)
        self._token_cache[cache_key] = (token, expiration)


token_manager = TokenManager()


class OAuthAccount:
    @property
    def email_scopes(self) -> list[str] | None:
        return None

    @property
    def contacts_scopes(self) -> list[str] | None:
        return None

    @property
    def calendar_scopes(self) -> list[str] | None:
        return None

    @property
    def scopes(self) -> list[str] | None:
        return None

    @declared_attr
    def refresh_token_id(cls):  # type: ignore[no-untyped-def]  # noqa: ANN201, N805
        return Column(ForeignKey(Secret.id), nullable=False)

    @declared_attr
    def secret(cls):  # type: ignore[no-untyped-def]  # noqa: ANN201, N805
        return relationship(
            "Secret",
            cascade="all",
            uselist=False,
            lazy="joined",
            foreign_keys=[cls.refresh_token_id],
        )

    @property
    def refresh_token(self) -> str | None:
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
    def refresh_token(self, value: str | bytes) -> None:
        # Must be a valid UTF-8 byte sequence without NULL bytes.
        if not isinstance(value, bytes):
            value = value.encode("utf-8")

        try:
            value.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("Invalid refresh_token")  # noqa: B904

        if b"\x00" in value:
            raise ValueError("Invalid refresh_token")

        log_token_usage(
            "refresh token stored", refresh_token=value, account=self
        )
        self.set_secret(SecretType.Token, value)

    def set_secret(self, secret_type: SecretType, secret_value: bytes) -> None:
        if not self.secret:
            self.secret = Secret()

        self.secret.type = secret_type.value
        self.secret.secret = secret_value

    def get_client_info(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        """
        Obtain the client ID and secret for this OAuth account.

        Return:
            Tuple with (client_id, client_secret).

        """
        if (
            not self.client_id  # type: ignore[attr-defined]
            or self.client_id  # type: ignore[attr-defined]
            == self.OAUTH_CLIENT_ID  # type: ignore[attr-defined]
        ):
            return (
                self.OAUTH_CLIENT_ID,  # type: ignore[attr-defined]
                self.OAUTH_CLIENT_SECRET,  # type: ignore[attr-defined]
            )
        else:
            raise OAuthError("No valid tokens.")

    def new_token(  # noqa: D417
        self, force_refresh: bool = False, scopes: list[str] | None = None
    ) -> tuple[str, int]:
        """
        Retrieves a new access token.

        Args:
            force_refresh (bool): Whether a token refresh should be forced when
                requesting it from an external token service (AuthAlligator)

        Returns:
            A tuple with the new access token and its expiration.

        Raises:
            OAuthError: If no token could be obtained.

        """  # noqa: D401
        try:
            return self.auth_handler.acquire_access_token(  # type: ignore[attr-defined]
                self, force_refresh=force_refresh, scopes=scopes
            )
        except Exception as e:
            log.warning(
                f"Error while getting access token: {e}",
                force_refresh=force_refresh,
                account_id=self.id,  # type: ignore[attr-defined]
                exc_info=True,
            )
            raise

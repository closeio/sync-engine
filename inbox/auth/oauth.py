import datetime
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional, Tuple

import pytz
import requests
from authalligator_client.client import Client as AuthAlligatorApiClient
from authalligator_client.enums import AccountErrorCode, ProviderType
from authalligator_client.exceptions import AccountError
from imapclient import IMAPClient

from inbox.config import config
from inbox.exceptions import (
    ConnectionError,
    ImapSupportDisabledError,
    OAuthError,
)
from inbox.logging import get_logger
from inbox.models.backends.oauth import OAuthAccount, token_manager
from inbox.models.secret import SecretType

from .base import AuthHandler

log = get_logger()


class OAuthAuthHandler(AuthHandler):
    # Defined by subclasses
    OAUTH_CLIENT_ID: Optional[str] = None
    OAUTH_CLIENT_SECRET: Optional[str] = None
    OAUTH_REDIRECT_URI: Optional[str] = None

    OAUTH_AUTHENTICATE_URL: Optional[str] = None
    OAUTH_ACCESS_TOKEN_URL: Optional[str] = None
    OAUTH_USER_INFO_URL: Optional[str] = None

    AUTHALLIGATOR_AUTH_KEY = config.get("AUTHALLIGATOR_AUTH_KEY")
    AUTHALLIGATOR_SERVICE_URL = config.get("AUTHALLIGATOR_SERVICE_URL")

    def _new_access_token_from_refresh_token(
        self, account: OAuthAccount
    ) -> Tuple[str, int]:
        refresh_token = account.refresh_token
        if not refresh_token:
            raise OAuthError("refresh_token required")

        client_id, client_secret = account.get_client_info()

        assert (
            self.OAUTH_ACCESS_TOKEN_URL
        ), "OAUTH_ACCESS_TOKEN_URL is not defined"
        access_token_url = self.OAUTH_ACCESS_TOKEN_URL

        data = urllib.parse.urlencode(
            {
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
            }
        )
        headers = {
            "Content-type": "application/x-www-form-urlencoded",
            "Accept": "text/plain",
        }

        account_logger = log.bind(account_id=account.id)

        try:
            response = requests.post(
                access_token_url, data=data, headers=headers
            )
        except requests.exceptions.ConnectionError as e:
            account_logger.error(
                "Network error renewing access token", error=e
            )
            raise ConnectionError()

        try:
            session_dict = response.json()
        except ValueError:
            account_logger.error(
                "Invalid JSON renewing on renewing token",
                response=response.text,
            )
            raise ConnectionError("Invalid JSON response on renewing token")

        if "error" in session_dict:
            if session_dict["error"] == "invalid_grant":
                # This is raised if the user has revoked access to the
                # application (or if the refresh token is otherwise invalid).
                raise OAuthError("invalid_grant")
            elif session_dict["error"] == "deleted_client":
                # If the developer has outright deleted their Google OAuth app
                # ID. We treat this too as a case of 'invalid credentials'.
                raise OAuthError("deleted_client")
            else:
                # You can also get e.g. {"error": "internal_failure"}
                account_logger.error(
                    "Error renewing access token", session_dict=session_dict
                )
                raise ConnectionError("Server error renewing access token")

        return session_dict["access_token"], session_dict["expires_in"]

    def _new_access_token_from_authalligator(
        self,
        account: OAuthAccount,
        force_refresh: bool,
        scopes: Optional[List[str]],
    ) -> Tuple[str, int]:
        """
        Return the access token based on an account created in AuthAlligator.
        """
        assert account.secret.type == SecretType.AuthAlligator.value
        assert self.AUTHALLIGATOR_AUTH_KEY
        assert self.AUTHALLIGATOR_SERVICE_URL

        aa_client = AuthAlligatorApiClient(
            token=self.AUTHALLIGATOR_AUTH_KEY,
            service_url=self.AUTHALLIGATOR_SERVICE_URL,
        )
        aa_data = json.loads(account.secret.secret)
        provider = ProviderType(aa_data["provider"])
        username = aa_data["username"]
        account_key = aa_data["account_key"]

        max_retries = 5
        for _ in range(max_retries):
            log.info(
                "requesting new access token from AuthAlligator",
                force_refresh=force_refresh,
                scopes=scopes,
            )
            # TODO - handle force_refresh once it's supported
            # by AuthAlligator and its client.
            try:
                aa_response = aa_client.query_account(
                    provider=provider,
                    username=username,
                    account_key=account_key,
                    scopes=scopes,
                )
                aa_account = aa_response
            except AccountError as exc:
                log.warning(
                    "AccountError during AuthAlligator account query",
                    account_id=account.id,
                    error_code=exc.code and exc.code.value,
                    error_message=exc.message,  # noqa: B306
                    retry_in=exc.retry_in,
                )
                if exc.code in (
                    AccountErrorCode.AUTHORIZATION_ERROR,
                    AccountErrorCode.CONFIGURATION_ERROR,
                    AccountErrorCode.DOES_NOT_EXIST,
                ):
                    raise OAuthError(
                        "Could not obtain access token from AuthAlligator"
                    ) from exc
                elif exc.code in (
                    AccountErrorCode.LOCK_ERROR,
                    AccountErrorCode.TRY_LATER,
                ):
                    time.sleep(exc.retry_in)
                else:
                    raise ConnectionError(
                        "Unexpected error while obtaining access token from AuthAlligator"
                    ) from exc
            else:
                now = datetime.datetime.now(pytz.UTC)
                access_token = aa_account.access_token
                expires_in = int(
                    (aa_account.access_token_expires_at - now).total_seconds()
                )
                assert expires_in > 0
                return (access_token, expires_in)

        raise ConnectionError(
            "Temporary error while obtaining access token from AuthAlligator. "
            "Max retries reached"
        )

    def acquire_access_token(
        self,
        account: OAuthAccount,
        force_refresh: bool = False,
        scopes: Optional[List[str]] = None,
    ) -> Tuple[str, int]:
        """
        Acquire a new access token for the given account.

        Args:
            force_refresh (bool): Whether a token refresh should be forced when
                requesting it from an external token service (AuthAlligator)
            scopes (Optional[List[str]]): Specific scopes for the
                requested access token (AuthAlligator only)

        Raises:
            OAuthError: If the token is no longer valid and syncing should stop.
            ConnectionError: If there was a temporary/connection error renewing
                the auth token.
        """
        if account.secret.type == SecretType.AuthAlligator.value:
            return self._new_access_token_from_authalligator(
                account, force_refresh, scopes
            )
        elif account.secret.type == SecretType.Token.value:
            # Any token requested from the refresh token is refreshed already.
            return self._new_access_token_from_refresh_token(account)
        else:
            raise OAuthError("No supported secret found.")

    def authenticate_imap_connection(
        self, account: OAuthAccount, conn: IMAPClient
    ):
        token = token_manager.get_token(
            account, force_refresh=False, scopes=account.email_scopes
        )
        try:
            conn.oauth2_login(account.email_address, token)
        except IMAPClient.Error as original_exc:
            exc = _process_imap_exception(original_exc)

            # Raise all IMAP disabled errors except authentication_failed
            # error, which we handle differently.
            if (
                isinstance(exc, ImapSupportDisabledError)
                and exc.reason != "authentication_failed"
            ):
                raise exc from original_exc

            log.warning(
                "Error during IMAP XOAUTH2 login",
                account_id=account.id,
                error=exc,
            )
            if not isinstance(exc, ImapSupportDisabledError):
                raise  # Unknown IMAPClient error, reraise

            # If we got an AUTHENTICATIONFAILED response, force a token refresh
            # and try again. If IMAP auth still fails, it's likely that IMAP
            # access is disabled, so propagate that errror.
            token = token_manager.get_token(
                account, force_refresh=True, scopes=account.email_scopes
            )
            try:
                conn.oauth2_login(account.email_address, token)
            except IMAPClient.Error as original_exc:
                exc = _process_imap_exception(original_exc)
                if (
                    not isinstance(exc, ImapSupportDisabledError)
                    or exc.reason != "authentication_failed"
                ):
                    raise exc from original_exc
                else:
                    # Instead of authentication_failed, report imap disabled
                    raise ImapSupportDisabledError(
                        "imap_disabled_for_account"
                    ) from original_exc

    def _get_user_info(self, session_dict):
        access_token = session_dict["access_token"]
        assert self.OAUTH_USER_INFO_URL, "OAUTH_USER_INFO_URL is not defined"
        request = urllib.request.Request(
            self.OAUTH_USER_INFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        try:
            response = urllib.request.urlopen(request)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise OAuthError("Could not retrieve user info.")
            log.error("user_info_fetch_failed", error_code=e.code, error=e)
            raise ConnectionError()
        except urllib.error.URLError as e:
            log.error("user_info_fetch_failed", error=e)
            raise ConnectionError()

        userinfo_dict = json.loads(response.read())

        return {"email": userinfo_dict["EmailAddress"]}

    def _get_authenticated_user(self, authorization_code):
        args = {
            "client_id": self.OAUTH_CLIENT_ID,
            "client_secret": self.OAUTH_CLIENT_SECRET,
            "redirect_uri": self.OAUTH_REDIRECT_URI,
            "code": authorization_code,
            "grant_type": "authorization_code",
        }

        headers = {
            "Content-type": "application/x-www-form-urlencoded",
            "Accept": "text/plain",
        }
        data = urllib.parse.urlencode(args)
        assert (
            self.OAUTH_ACCESS_TOKEN_URL
        ), "OAUTH_ACCESS_TOKEN_URL is not defined"
        resp = requests.post(
            self.OAUTH_ACCESS_TOKEN_URL, data=data, headers=headers
        )

        session_dict = resp.json()

        if "error" in session_dict:
            raise OAuthError(session_dict["error"])

        userinfo_dict = self._get_user_info(session_dict)

        z = session_dict.copy()
        z.update(userinfo_dict)

        return z


class OAuthRequestsWrapper(requests.auth.AuthBase):
    """Helper class for setting the Authorization header on HTTP requests."""

    def __init__(self, token):
        self.token = token

    def __call__(self, r):
        r.headers["Authorization"] = f"Bearer {self.token}"
        return r


def _process_imap_exception(exc):
    message = exc.args[0] if exc.args else ""
    if "Lookup failed" in message:
        # Gmail is disabled for this apps account
        return ImapSupportDisabledError("gmail_disabled_for_domain")
    elif "IMAP access is disabled for your domain." in message:
        # IMAP is disabled for this domain
        return ImapSupportDisabledError("imap_disabled_for_domain")
    elif message.startswith(  # noqa: SIM114
        "[AUTHENTICATIONFAILED] Invalid credentials (Failure)"
    ):
        # Google
        return ImapSupportDisabledError("authentication_failed")
    elif message.startswith("AUTHENTICATE failed."):
        # Microsoft
        return ImapSupportDisabledError("authentication_failed")
    else:
        # Unknown IMAPClient error
        return exc

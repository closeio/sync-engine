class AuthError(Exception):
    pass


class SSLNotSupportedError(AuthError):
    pass


class ConnectionError(AuthError):  # noqa: A001
    pass


class ValidationError(AuthError):
    pass


class NotSupportedError(AuthError):
    pass


class OAuthError(ValidationError):
    pass


class UserRecoverableConfigError(Exception):
    pass


class GmailSettingError(ValidationError):
    pass


class ImapSupportDisabledError(ValidationError):
    def __init__(self, reason=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(reason)
        self.reason = reason


class AccessNotEnabledError(Exception):
    pass


class AppPasswordError(ValidationError):
    pass

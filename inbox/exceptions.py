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


class GmailDisabledError(Exception):
    pass


class IMAPDisabledError(Exception):
    pass


class AccessNotEnabledError(Exception):
    pass


class AppPasswordError(ValidationError):
    pass

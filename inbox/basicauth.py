# TODO(emfree): this is now legitimately just a grab-bag of nebulous
# exceptions.  Rename module and clean up.


class AuthError(Exception):
    pass


class SSLNotSupportedError(AuthError):
    pass


class ConnectionError(AuthError):
    pass


class ValidationError(AuthError):
    pass


class NotSupportedError(AuthError):
    pass


class OAuthError(ValidationError):
    pass


class ConfigurationError(Exception):
    pass


class UserRecoverableConfigError(Exception):
    pass


class SettingUpdateError(Exception):
    pass


class GmailSettingError(ValidationError):
    pass


class ImapSupportDisabledError(ValidationError):
    def __init__(self, reason=None):
        super().__init__(reason)
        self.reason = reason


class AccessNotEnabledError(Exception):
    pass


class AppPasswordError(ValidationError):
    pass

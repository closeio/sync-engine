import enum  # Python 3 style enums from enum34
from types import TracebackType

import nacl.secret
import nacl.utils

from inbox.config import config


class EncryptionScheme(enum.Enum):
    # No encryption
    NULL = 0

    # nacl.secret.SecretBox with a static key
    SECRETBOX_WITH_STATIC_KEY = 1


def get_encryption_oracle(secret_name):  # type: ignore[no-untyped-def]  # noqa: ANN201
    """
    Return an encryption oracle for the given secret.
    """
    assert secret_name in ("SECRET_ENCRYPTION_KEY", "BLOCK_ENCRYPTION_KEY")
    return _EncryptionOracle(secret_name)


def get_decryption_oracle(secret_name: str) -> "_DecryptionOracle":
    """
    Return an decryption oracle for the given secret.

    Decryption oracles can also encrypt.
    """
    assert secret_name in ("SECRET_ENCRYPTION_KEY", "BLOCK_ENCRYPTION_KEY")
    return _DecryptionOracle(secret_name)


class _EncryptionOracle:
    """
    This object is responsible for encryption only.

    In the future, it may interface with a subprocess or a hardware security
    module.
    """  # noqa: D404

    def __init__(self, secret_name) -> None:  # type: ignore[no-untyped-def]
        self._closed = False

        if not config.get_required("ENCRYPT_SECRETS"):
            self.default_scheme = EncryptionScheme.NULL
            self._secret_box = None
            return

        self.default_scheme = EncryptionScheme.SECRETBOX_WITH_STATIC_KEY
        self._secret_box = nacl.secret.SecretBox(
            key=config.get_required(secret_name),
            encoder=nacl.encoding.HexEncoder,
        )

    def __enter__(self):  # type: ignore[no-untyped-def]  # noqa: ANN204
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_obj: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.close()

    def __del__(self) -> None:
        if self._closed:
            return
        self.close()

    def close(self) -> None:
        if self._closed:
            # already closed
            return

        del self.default_scheme
        del self._secret_box
        self._closed = True

    def encrypt(  # type: ignore[no-untyped-def]
        self, plaintext, encryption_scheme=None
    ):
        """
        Encrypt the specified secret.  If no encryption_scheme is specified
        (recommended), a reasonable default will be used.

        Returns (ciphertext, encryption_scheme)
        """
        if self._closed:
            raise ValueError("Connection to crypto oracle already closed")

        # default args
        if encryption_scheme is None:
            encryption_scheme = self.default_scheme

        # sanity check
        if not isinstance(plaintext, bytes):
            raise TypeError("plaintext should be bytes, not unicode")
        if not isinstance(encryption_scheme, enum.Enum):
            raise TypeError("encryption_scheme should be an Enum")
        if not 0 <= encryption_scheme.value <= 2**31 - 1:
            raise ValueError("encryption_scheme value out of range")
        if (
            encryption_scheme != EncryptionScheme.NULL
            and not config.get_required("ENCRYPT_SECRETS")
        ):
            raise ValueError("ENCRYPT_SECRETS not enabled in config")

        # encrypt differently depending on the scheme
        if encryption_scheme == EncryptionScheme.NULL:
            ciphertext = plaintext

        elif encryption_scheme == EncryptionScheme.SECRETBOX_WITH_STATIC_KEY:
            assert self._secret_box
            ciphertext = self._secret_box.encrypt(
                plaintext=plaintext,
                nonce=nacl.utils.random(nacl.secret.SecretBox.NONCE_SIZE),
            )

        else:
            raise ValueError(
                f"encryption_scheme not supported: {encryption_scheme}"
            )

        return (ciphertext, encryption_scheme.value)


class _DecryptionOracle(_EncryptionOracle):
    """
    This object is responsible for encrypting and decrypting secrets.

    In the future, it may interface with a subprocess or a hardware security
    module.
    """  # noqa: D404

    def reencrypt(  # type: ignore[no-untyped-def]
        self, ciphertext, encryption_scheme, new_encryption_scheme=None
    ):
        """
        Re-encrypt the specified secret.  If no new_encryption_scheme is
        specified (recommended), a reasonable default will be used.

        If access to the decrypted secret is not needed, this API function
        should be used to re-encrypt secrets.  In the future, this will allow
        us to keep the decrypted secrets out of the application's memory.

        Returns (ciphertext, encryption_scheme)
        """
        if self._closed:
            raise ValueError("Connection to crypto oracle already closed")

        # for now, it's all in memory anyway
        return self.encrypt(
            self.decrypt(ciphertext, encryption_scheme),
            encryption_scheme=new_encryption_scheme,
        )

    def decrypt(  # type: ignore[no-untyped-def]
        self, ciphertext, encryption_scheme
    ):
        # type (bytes, int) -> bytes
        """
        Decrypt the specified secret.

        Returns the plaintext as bytes.
        """
        if self._closed:
            raise ValueError("Connection to crypto oracle already closed")

        encryption_scheme_value = encryption_scheme  # expect an Enum value

        # sanity check
        if not isinstance(ciphertext, bytes):
            raise TypeError("ciphertext should be bytes, not unicode")
        if not isinstance(encryption_scheme_value, int):
            raise TypeError("encryption_scheme_value should be a number")
        if not 0 <= encryption_scheme_value <= 2**31 - 1:
            raise ValueError("encryption_scheme_value out of range")

        # decrypt differently depending on the scheme
        if encryption_scheme_value == EncryptionScheme.NULL.value:
            return ciphertext

        elif (
            encryption_scheme_value
            == EncryptionScheme.SECRETBOX_WITH_STATIC_KEY.value
        ):
            assert self._secret_box
            return self._secret_box.decrypt(ciphertext)

        else:
            raise ValueError(
                f"encryption_scheme not supported: {encryption_scheme_value}"
            )

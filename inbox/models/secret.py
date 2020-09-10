import enum

from sqlalchemy import Column, Enum, Integer
from sqlalchemy.orm import validates
from sqlalchemy.types import BLOB

from inbox.models.base import MailSyncBase
from inbox.models.mixins import DeletedAtMixin, UpdatedAtMixin
from inbox.security.oracles import get_decryption_oracle, get_encryption_oracle


class SecretType(enum.Enum):
    Password = "password"
    Token = "token"
    AuthAlligator = "authalligator"


class Secret(MailSyncBase, UpdatedAtMixin, DeletedAtMixin):
    """Simple local secrets table."""

    _secret = Column(BLOB, nullable=False)

    # Type of secret
    type = Column(
        Enum(SecretType, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )

    # Scheme used
    encryption_scheme = Column(Integer, server_default="0", nullable=False)

    @property
    def secret(self):
        with get_decryption_oracle("SECRET_ENCRYPTION_KEY") as d_oracle:
            return d_oracle.decrypt(
                self._secret, encryption_scheme=self.encryption_scheme
            )

    @secret.setter
    def secret(self, plaintext):
        """
        The secret must be a byte sequence.
        """
        if not isinstance(plaintext, bytes):
            raise TypeError("Invalid secret")

        with get_encryption_oracle("SECRET_ENCRYPTION_KEY") as e_oracle:
            self._secret, self.encryption_scheme = e_oracle.encrypt(plaintext)

    @validates("type")
    def validate_type(self, k, type):
        if type not in SECRET_TYPES:
            raise TypeError("Invalid secret type.")

        return type

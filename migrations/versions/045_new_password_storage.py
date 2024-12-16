"""
Store passwords in plaintext.

Revision ID: 7a117720554
Revises: 247cd689758c
Create Date: 2014-06-30 20:36:30.705550

"""

# revision identifiers, used by Alembic.
revision = "7a117720554"
down_revision = "247cd689758c"

import os
from typing import Never

import sqlalchemy as sa  # type: ignore[import-untyped]
from alembic import op

# We're deleting this value from the config, so need to explicitly give it for
# this migration.
# If you're running this migration and for some reason you had specified a
# different key directory, you should change this accordingly.
KEY_DIR = "/var/lib/inboxapp/keys"


# Copied from deprecated inbox.util.cryptography module.
# Needed to port passwords to new storage method.
def decrypt_aes(ciphertext, key):  # type: ignore[no-untyped-def]  # noqa: ANN201
    """
    Decrypts a ciphertext that was AES-encrypted with the given key.
    The function expects the ciphertext as a byte string and it returns the
    decrypted message as a byte string.
    """
    from Crypto.Cipher import AES  # type: ignore[import-not-found]

    def unpad(s):  # type: ignore[no-untyped-def]
        return s[: -ord(s[-1])]

    iv = ciphertext[: AES.block_size]
    cipher = AES.new(key, AES.MODE_CBC, iv)
    plaintext = unpad(cipher.decrypt(ciphertext))[AES.block_size :]
    return plaintext


def upgrade() -> None:
    from inbox.ignition import main_engine  # type: ignore[attr-defined]
    from inbox.models.session import session_scope

    engine = main_engine(pool_size=1, max_overflow=0)
    from hashlib import sha256

    from inbox.util.file import mkdirp  # type: ignore[attr-defined]

    OriginalBase = sa.ext.declarative.declarative_base()  # noqa: N806
    OriginalBase.metadata.reflect(engine)

    if "easaccount" in OriginalBase.metadata.tables:
        op.add_column("easaccount", sa.Column("password", sa.String(256)))

        # Reflect again to pick up added column
        Base = sa.ext.declarative.declarative_base()  # noqa: N806
        Base.metadata.reflect(engine)

        class Account(Base):  # type: ignore[misc, valid-type]
            __table__ = Base.metadata.tables["account"]

        class EASAccount(Account):
            __table__ = Base.metadata.tables["easaccount"]

            @property
            def _keyfile(  # type: ignore[no-untyped-def]  # noqa: PLR0206
                self, create_dir: bool = True
            ):
                assert self.key

                assert KEY_DIR
                if create_dir:
                    mkdirp(KEY_DIR)
                key_filename = f"{sha256(self.key).hexdigest()}"
                return os.path.join(KEY_DIR, key_filename)  # noqa: PTH118

            def get_old_password(self):  # type: ignore[no-untyped-def]
                if self.password_aes is not None:
                    with open(self._keyfile) as f:  # noqa: PTH123
                        key = f.read()

                    key = self.key + key
                    return decrypt_aes(self.password_aes, key)
                return None

        with session_scope() as db_session:  # type: ignore[call-arg]
            for account in db_session.query(EASAccount):
                account.password = account.get_old_password()
                db_session.add(account)
            db_session.commit()

    op.drop_column("account", "password_aes")
    op.drop_column("account", "key")


def downgrade() -> Never:
    raise Exception("No rolling back")

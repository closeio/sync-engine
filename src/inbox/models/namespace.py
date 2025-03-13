from sqlalchemy import (  # type: ignore[import-untyped]
    BigInteger,
    Column,
    ForeignKey,
    bindparam,
)
from sqlalchemy.orm import (  # type: ignore[import-untyped]
    backref,
    relationship,
)

from inbox.models.base import MailSyncBase
from inbox.models.mixins import DeletedAtMixin, HasPublicID, UpdatedAtMixin


class Namespace(MailSyncBase, HasPublicID, UpdatedAtMixin, DeletedAtMixin):
    account_id = Column(
        BigInteger, ForeignKey("account.id", ondelete="CASCADE"), nullable=True
    )
    account = relationship(
        "Account",
        lazy="joined",
        single_parent=True,
        backref=backref(
            "namespace",
            uselist=False,
            lazy="joined",
            passive_deletes=True,
            cascade="all,delete-orphan",
        ),
        uselist=False,
    )

    def __str__(self) -> str:
        return "{} <{}>".format(
            self.public_id,
            (self.account.email_address if self.account else ""),
        )

    @property
    def email_address(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        if self.account is not None:
            return self.account.email_address
        return None

    @classmethod
    def get(cls, id_, session):  # type: ignore[no-untyped-def]  # noqa: ANN206
        q = session.query(cls)
        q = q.filter(cls.id == bindparam("id_"))
        return q.params(id_=id_).first()

    @classmethod
    def from_public_id(  # type: ignore[no-untyped-def]  # noqa: ANN206
        cls, public_id, db_session
    ):
        q = db_session.query(Namespace)
        q = q.filter(Namespace.public_id == bindparam("public_id"))
        return q.params(public_id=public_id).one()

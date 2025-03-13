from sqlalchemy import BigInteger, Column  # type: ignore[import-untyped]
from sqlalchemy.ext.declarative import (  # type: ignore[import-untyped]
    as_declarative,
    declared_attr,
)
from sqlalchemy.orm.exc import (  # type: ignore[import-untyped]
    DetachedInstanceError,
)

from inbox.models.mixins import CreatedAtMixin


@as_declarative()
class MailSyncBase(CreatedAtMixin):
    """
    Provides automated table name, primary key column, and created_at timestamp.

    """

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    @declared_attr
    def __tablename__(cls):  # type: ignore[no-untyped-def]  # noqa: ANN204, N805
        return cls.__name__.lower()  # type: ignore[attr-defined]

    @declared_attr
    def __table_args__(cls):  # type: ignore[no-untyped-def]  # noqa: ANN204, N805
        return {"extend_existing": True}

    def __repr__(self) -> str:
        try:
            return "<{} (id: {})>".format(
                self.__module__ + "." + self.__class__.__name__, self.id
            )
        except DetachedInstanceError:
            # SQLAlchemy has expired all values for this object and is trying
            # to refresh them from the database, but has no session for the
            # refresh.
            return "<{} (id: detached)>".format(
                self.__module__ + "." + self.__class__.__name__
            )

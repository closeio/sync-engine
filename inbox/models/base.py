from sqlalchemy import Column, BigInteger
from sqlalchemy.ext.declarative import as_declarative, declared_attr
from sqlalchemy.orm.exc import DetachedInstanceError

from inbox.models.mixins import CreatedAtMixin


@as_declarative()
class MailSyncBase(CreatedAtMixin):
    """
    Provides automated table name, primary key column, and created_at timestamp.

    """

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    @declared_attr
    def __tablename__(cls):
        return cls.__name__.lower()

    @declared_attr
    def __table_args__(cls):
        return {"extend_existing": True}

    def __repr__(self):
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

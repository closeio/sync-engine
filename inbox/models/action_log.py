from sqlalchemy import (
    BigInteger,
    Column,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    desc,
)
from sqlalchemy.orm import relationship

from inbox.logging import get_logger
from inbox.models.base import MailSyncBase
from inbox.models.mixins import DeletedAtMixin, UpdatedAtMixin
from inbox.models.namespace import Namespace
from inbox.sqlalchemy_ext.util import JSON

log = get_logger()


def schedule_action(
    func_name, record, namespace_id, db_session, **kwargs
) -> None:
    # Ensure that the record's id is non-null
    db_session.flush()

    account = db_session.query(Namespace).get(namespace_id).account

    # Don't queue action if an existing pending action exists.
    existing_log_entry = (
        db_session.query(ActionLog)
        .filter(
            ActionLog.discriminator == "actionlog",
            ActionLog.status == "pending",
            ActionLog.namespace_id == namespace_id,
            ActionLog.action == func_name,
            ActionLog.record_id == record.id,
        )
        .order_by(desc(ActionLog.id))
        .first()
    )
    if existing_log_entry and existing_log_entry.extra_args == kwargs:
        return

    log_entry = account.actionlog_cls.create(
        action=func_name,
        table_name=record.__tablename__,
        record_id=record.id,
        namespace_id=namespace_id,
        extra_args=kwargs,
    )
    db_session.add(log_entry)


class ActionLog(MailSyncBase, UpdatedAtMixin, DeletedAtMixin):
    namespace_id = Column(
        ForeignKey(Namespace.id, ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    namespace = relationship("Namespace")

    action = Column(Text(40), nullable=False)
    record_id = Column(BigInteger, nullable=False)
    table_name = Column(Text(40), nullable=False)
    status = Column(
        Enum("pending", "successful", "failed"), server_default="pending"
    )
    retries = Column(Integer, server_default="0", nullable=False)

    extra_args = Column(JSON, nullable=True)

    @classmethod
    def create(cls, action, table_name, record_id, namespace_id, extra_args):
        return cls(
            action=action,
            table_name=table_name,
            record_id=record_id,
            namespace_id=namespace_id,
            extra_args=extra_args,
        )

    discriminator = Column("type", String(16))
    __mapper_args__ = {
        "polymorphic_identity": "actionlog",
        "polymorphic_on": discriminator,
    }


Index(
    "ix_actionlog_status_namespace_id_record_id",
    ActionLog.status,
    ActionLog.namespace_id,
    ActionLog.record_id,
)

Index(
    "ix_actionlog_namespace_id_status_type",
    ActionLog.namespace_id,
    ActionLog.status,
    ActionLog.discriminator,
)

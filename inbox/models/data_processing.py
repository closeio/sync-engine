import datetime
import json
import zlib

from sqlalchemy import (  # type: ignore[import-untyped]
    Column,
    DateTime,
    ForeignKey,
)
from sqlalchemy.dialects.mysql import (  # type: ignore[import-untyped]
    MEDIUMBLOB,
)
from sqlalchemy.schema import UniqueConstraint  # type: ignore[import-untyped]

from inbox.models.base import MailSyncBase
from inbox.models.mixins import DeletedAtMixin, UpdatedAtMixin
from inbox.models.namespace import Namespace


class DataProcessingCache(MailSyncBase, UpdatedAtMixin, DeletedAtMixin):
    """Cached data used in data processing"""

    namespace_id = Column(
        ForeignKey(Namespace.id, ondelete="CASCADE"), nullable=False
    )
    _contact_rankings = Column("contact_rankings", MEDIUMBLOB)
    _contact_groups = Column("contact_groups", MEDIUMBLOB)
    contact_rankings_last_updated = Column(DateTime)
    contact_groups_last_updated = Column(DateTime)

    @property
    def contact_rankings(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        if self._contact_rankings is None:
            return None
        else:
            return json.loads(zlib.decompress(self._contact_rankings))

    @contact_rankings.setter
    def contact_rankings(self, value) -> None:  # type: ignore[no-untyped-def]
        self._contact_rankings = zlib.compress(
            json.dumps(value).encode("utf-8")
        )
        self.contact_rankings_last_updated = datetime.datetime.now()

    @property
    def contact_groups(self):  # type: ignore[no-untyped-def]  # noqa: ANN201
        if self._contact_groups is None:
            return None
        else:
            return json.loads(zlib.decompress(self._contact_groups))

    @contact_groups.setter
    def contact_groups(self, value) -> None:  # type: ignore[no-untyped-def]
        self._contact_groups = zlib.compress(json.dumps(value).encode("utf-8"))
        self.contact_groups_last_updated = datetime.datetime.now()

    __table_args__ = (UniqueConstraint("namespace_id"),)

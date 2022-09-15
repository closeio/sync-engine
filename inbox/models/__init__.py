"""
Caution: subtleties ahead.

It's desirable to ensure that all SQLAlchemy models are imported before you
try to issue any sort of query. The reason you want this assurance is because
if you have mutually dependent relationships between models in separate
files, at least one of those relationships must be specified by a string
reference, in order to avoid circular import errors. But if you haven't
actually imported the referenced model by query time, SQLAlchemy can't resolve
the reference.
"""

from inbox.models.account import Account
from inbox.models.action_log import ActionLog
from inbox.models.backends import module_registry as backend_module_registry
from inbox.models.base import MailSyncBase
from inbox.models.block import Block, Part
from inbox.models.calendar import Calendar
from inbox.models.category import Category
from inbox.models.contact import (
    Contact,
    EventContactAssociation,
    MessageContactAssociation,
    PhoneNumber,
)
from inbox.models.data_processing import DataProcessingCache
from inbox.models.event import Event
from inbox.models.folder import Folder
from inbox.models.label import Label
from inbox.models.message import Message, MessageCategory
from inbox.models.metadata import Metadata
from inbox.models.namespace import Namespace
from inbox.models.search import ContactSearchIndexCursor
from inbox.models.secret import Secret
from inbox.models.thread import Thread
from inbox.models.transaction import AccountTransaction, Transaction
from inbox.models.when import Date, DateSpan, Time, TimeSpan, When

__all__ = [
    "Account",
    "MailSyncBase",
    "ActionLog",
    "Block",
    "Part",
    "MessageContactAssociation",
    "Contact",
    "PhoneNumber",
    "Calendar",
    "DataProcessingCache",
    "Event",
    "EventContactAssociation",
    "Folder",
    "Message",
    "Namespace",
    "ContactSearchIndexCursor",
    "Secret",
    "Thread",
    "Transaction",
    "When",
    "Time",
    "TimeSpan",
    "Date",
    "DateSpan",
    "Label",
    "Category",
    "MessageCategory",
    "Metadata",
    "AccountTransaction",
]

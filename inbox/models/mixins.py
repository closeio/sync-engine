import abc
from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, String, func, inspect, sql
from sqlalchemy.ext.hybrid import Comparator, hybrid_property

from inbox.models.constants import MAX_INDEXABLE_LENGTH
from inbox.sqlalchemy_ext.util import ABCMixin, Base36UID, generate_public_id
from inbox.util.addr import canonicalize_address
from inbox.util.encoding import unicode_safe_truncate


class HasRevisions(ABCMixin):
    """Mixin for tables that should be versioned in the transaction log."""

    @property
    def versioned_relationships(self):
        """
        May be overriden by subclasses. This should be the list of
        relationship attribute names that should trigger an update revision
        when changed. (We want to version changes to some, but not all,
        relationship attributes.)

        """
        return []

    @property
    def propagated_attributes(self):
        """
        May be overridden by subclasses. This is the list of attribute names
        that should trigger an update revision for a /related/ object -
        for example, when a message's `is_read` or `categories` is changed,
        we want an update revision created for the message's thread as well.
        Such manual propagation is required because changes to related objects
        are not reflected in the related attribute's history, only additions
        and deletions are. For example, thread.messages.history will
        not reflect a change made to one of the thread's messages.

        """
        return []

    @property
    def should_suppress_transaction_creation(self):
        """
        May be overridden by subclasses. We don't want to version certain
        specific objects - for example, Block instances that are just raw
        message parts and not real attachments. Use this property to suppress
        revisions of such objects. (The need for this is really an artifact of
        current deficiencies in our models. We should be able to get rid of it
        eventually.)

        """
        return False

    # Must be defined by subclasses
    API_OBJECT_NAME = abc.abstractproperty()

    def has_versioned_changes(self):
        """
        Return True if the object has changes on any of its column properties
        or any relationship attributes named in self.versioned_relationships,
        or has been manually marked as dirty (the special 'dirty' instance
        attribute is set to True).

        """
        obj_state = inspect(self)

        versioned_attribute_names = list(self.versioned_relationships)
        for mapper in obj_state.mapper.iterate_to_root():
            for attr in mapper.column_attrs:
                versioned_attribute_names.append(attr.key)

        for attr_name in versioned_attribute_names:
            if getattr(obj_state.attrs, attr_name).history.has_changes():
                return True

        return False


class HasPublicID:
    public_id = Column(
        Base36UID, nullable=False, index=True, default=generate_public_id
    )


class AddressComparator(Comparator):
    def __eq__(self, other):
        return self.__clause_element__() == canonicalize_address(other)

    def like(self, term, escape=None):
        return self.__clause_element__().like(term, escape=escape)

    def in_(self, addresses):
        return self.__clause_element__().in_(
            [canonicalize_address(address) for address in addresses]
        )


class CaseInsensitiveComparator(Comparator):
    def __eq__(self, other):
        return func.lower(self.__clause_element__()) == func.lower(other)


class HasEmailAddress:
    """
    Provides an email_address attribute, which returns as value whatever you
    set it to, but uses a canonicalized form for comparisons. So e.g.
        db_session.query(Account).filter_by(
           email_address='ben.bitdiddle@gmail.com').all()
    and
        db_session.query(Account).filter_by(
           email_address='ben.bitdiddle@gmail.com').all()
    will return the same results, because the two Gmail addresses are
    equivalent.

    """

    _raw_address = Column(String(MAX_INDEXABLE_LENGTH), nullable=True, index=True)
    _canonicalized_address = Column(
        String(MAX_INDEXABLE_LENGTH), nullable=True, index=True
    )

    @hybrid_property
    def email_address(self):
        return self._raw_address

    @email_address.comparator
    def email_address(cls):
        return AddressComparator(cls._canonicalized_address)

    @email_address.setter
    def email_address(self, value):
        # Silently truncate if necessary. In practice, this may be too
        # long if somebody put a super-long email into their contacts by
        # mistake or something.
        if value is not None:
            value = unicode_safe_truncate(value, MAX_INDEXABLE_LENGTH)
        self._raw_address = value
        self._canonicalized_address = canonicalize_address(value)


class CreatedAtMixin:
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)


class UpdatedAtMixin:
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
        index=True,
    )


class DeletedAtMixin:
    deleted_at = Column(DateTime, nullable=True, index=True)


class HasRunState(ABCMixin):
    # Track whether this object (e.g. folder, account) should be running
    # or not. Used to compare against reported data points to see if all is
    # well.

    # Is sync enabled for this object? The sync_enabled property should be
    # a Boolean that reflects whether the object should be reporting
    # a heartbeat. For folder-level objects, this property can be used to
    # combine local run state with the parent account's state, so we don't
    # need to cascade account-level start/stop status updates down to folders.
    sync_enabled = abc.abstractproperty()

    # Database-level tracking of whether the sync should be running.
    sync_should_run = Column(
        Boolean, default=True, nullable=False, server_default=sql.expression.true()
    )

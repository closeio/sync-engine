import codecs
import contextlib
import re
import struct
import uuid
import weakref
from typing import Any, MutableMapping, Optional, Tuple

from sqlalchemy import String, Text, event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.mutable import Mutable
from sqlalchemy.pool import QueuePool
from sqlalchemy.sql import operators
from sqlalchemy.types import BINARY, TypeDecorator

from inbox.logging import get_logger
from inbox.sqlalchemy_ext import json_util
from inbox.util.encoding import base36decode, base36encode

log = get_logger()


MAX_SANE_QUERIES_PER_SESSION = 100
MAX_TEXT_BYTES = 65535
MAX_BYTES_PER_CHAR = 4  # For collation of utf8mb4
MAX_TEXT_CHARS = int(MAX_TEXT_BYTES / float(MAX_BYTES_PER_CHAR))
MAX_MYSQL_INTEGER = 2147483647


query_counts: MutableMapping[Any, int] = weakref.WeakKeyDictionary()
should_log_dubiously_many_queries = True


# When setting up the DB for tests we do a bunch of queries all at once which
# triggers the dreaded dubiously many queries warning. This allows us to avoid
# that. Don't use this to silence any warnings in application code because
# these warnings are an indicator of excessive lazy loading from the DB.
@contextlib.contextmanager
def disabled_dubiously_many_queries_warning():
    global should_log_dubiously_many_queries
    should_log_dubiously_many_queries = False
    yield
    should_log_dubiously_many_queries = True


@event.listens_for(Engine, "before_cursor_execute")
def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    if conn not in query_counts:
        query_counts[conn] = 1
    else:
        query_counts[conn] += 1


@event.listens_for(Engine, "commit")
def before_commit(conn):
    if not should_log_dubiously_many_queries:
        return
    if query_counts.get(conn, 0) > MAX_SANE_QUERIES_PER_SESSION:
        log.warning(
            "Dubiously many queries per session!", query_count=query_counts.get(conn)
        )


class ABCMixin:
    """Use this if you want a mixin class which is actually an abstract base
    class, for example in order to enforce that concrete subclasses define
    particular methods or properties.
    """

    __abstract__ = True


# Column Types


class StringWithTransform(TypeDecorator):
    """
    Column type that extends sqlalchemy.String so that any strings of
    this type will be applied a user defined transform before saving them to the
    database, and will make sure that any `==` queries executed against a Column
    of this type match the values that we are actually storing in the database.

    Note that this will only apply the transform at the database level, before
    saving it, so column field in the model instance will /not/ have the
    transform applied. If you want to make sure that all model instances have
    the transform applied, you must manually apply it using a custom property
    setter or a @validates decorator
    """

    cache_ok = True

    impl = String

    def __init__(self, string_transform, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if string_transform is None:
            raise ValueError("Must provide a string_transform")
        if not callable(string_transform):
            raise TypeError("`string_transform` must be callable")
        self._string_transform = string_transform

    def process_bind_param(self, value, dialect):
        return self._string_transform(value)

    class comparator_factory(String.Comparator):
        def __eq__(self, other):
            other = self.type._string_transform(other)
            return self.operate(operators.eq, other)


# http://docs.sqlalchemy.org/en/rel_0_9/core/types.html#marshal-json-strings
class JSON(TypeDecorator):
    cache_ok = True

    impl = Text

    def process_bind_param(self, value, dialect):
        if value is None:
            return None

        return json_util.dumps(value)

    def process_result_value(self, value, dialect):
        if not value:
            return None

        # Unfortunately loads() is strict about invalid utf-8 whereas dumps()
        # is not. This can result in ValueErrors during decoding - we simply
        # log and return None for now.
        # http://bugs.python.org/issue11489
        try:
            return json_util.loads(value)
        except ValueError:
            log.error("ValueError on decoding JSON", value=value)


def json_field_too_long(value):
    return len(json_util.dumps(value)) > MAX_TEXT_CHARS


class LittleJSON(JSON):
    impl = String(255)


class BigJSON(JSON):
    # if all characters were 4-byte, this would fit in mysql's MEDIUMTEXT
    impl = Text(4194304)


class Base36UID(TypeDecorator):
    cache_ok = True

    impl = BINARY(16)  # 128 bit unsigned integer

    def process_bind_param(self, value: Optional[str], dialect: Any) -> Optional[bytes]:
        if not value:
            return None
        return b36_to_bin(value)

    def process_result_value(
        self, value: Optional[bytes], dialect: Any
    ) -> Optional[str]:
        return int128_to_b36(value)


# http://bit.ly/1LbMnqu
# Can simply use this as is because though we use bson.json_util, loads()
# dumps() return standard Python dicts like the json.* equivalents
# (because these are simply called under the hood)
class MutableDict(Mutable, dict):
    @classmethod
    def coerce(cls, key, value):
        """Convert plain dictionaries to MutableDict."""
        if not isinstance(value, MutableDict):
            if isinstance(value, dict):
                return MutableDict(value)

            # this call will raise ValueError
            return Mutable.coerce(key, value)
        else:
            return value

    def __setitem__(self, key, value):
        """Detect dictionary set events and emit change events."""
        dict.__setitem__(self, key, value)
        self.changed()

    def __delitem__(self, key):
        """Detect dictionary del events and emit change events."""
        dict.__delitem__(self, key)
        self.changed()

    def update(self, *args, **kwargs):
        for k, v in dict(*args, **kwargs).items():
            self[k] = v

    # To support pickling:
    def __getstate__(self):
        return dict(self)

    def __setstate__(self, state):
        self.update(state)


class MutableList(Mutable, list):
    @classmethod
    def coerce(cls, key, value):
        """Convert plain list to MutableList"""
        if not isinstance(value, MutableList):
            if isinstance(value, list):
                return MutableList(value)

            # this call will raise ValueError
            return Mutable.coerce(key, value)
        else:
            return value

    def __setitem__(self, idx, value):
        list.__setitem__(self, idx, value)
        self.changed()

    def __delitem__(self, idx):
        list.__delitem__(self, idx)
        self.changed()

    def append(self, value):
        list.append(self, value)
        self.changed()

    def insert(self, idx, value):
        list.insert(self, idx, value)
        self.changed()

    def extend(self, values):
        list.extend(self, values)
        self.changed()

    def pop(self, *args, **kw):
        value = list.pop(self, *args, **kw)
        self.changed()
        return value

    def remove(self, value):
        list.remove(self, value)
        self.changed()


def int128_to_b36(int128: Optional[bytes]) -> Optional[str]:
    """int128: a 128 bit unsigned integer
    returns a base-36 string representation
    """
    if not int128:
        return None
    assert len(int128) == 16, "should be 16 bytes (128 bits)"
    a, b = struct.unpack(">QQ", int128)  # uuid() is big-endian
    pub_id = (a << 64) | b
    return base36encode(pub_id).lower()


def b36_to_bin(b36_string: str) -> bytes:
    """b36_string: a base-36 encoded string
    returns binary 128 bit unsigned integer
    """
    int128 = base36decode(b36_string)
    MAX_INT64 = 0xFFFFFFFFFFFFFFFF
    return struct.pack(">QQ", (int128 >> 64) & MAX_INT64, int128 & MAX_INT64)


def generate_public_id() -> str:
    """Returns a base-36 string UUID"""
    u = uuid.uuid4().bytes
    result = int128_to_b36(u)
    assert result
    return result


# Other utilities

RE_SURROGATE_CHARACTER = re.compile(r"[\ud800-\udfff]")
RE_SURROGATE_PAIR = re.compile(r"[\ud800-\udbff][\udc00-\udfff]")


def utf8_encode(text: str, errors: str = "strict") -> Tuple[bytes, int]:
    return text.encode("utf-8", errors), len(text)


def utf8_surrogate_fix_decode(
    memory: memoryview, errors: str = "strict"
) -> Tuple[str, int]:
    binary = memory.tobytes()

    with contextlib.suppress(UnicodeDecodeError):
        return binary.decode("utf-8", errors), len(binary)

    text = binary.decode("utf-8", "surrogatepass")

    # now fix surrogate pairs, we can recover those
    for surrogate_pair in set(re.findall(RE_SURROGATE_PAIR, text)):
        text = text.replace(
            surrogate_pair,
            surrogate_pair.encode("utf-16", "surrogatepass").decode("utf-16"),
        )

    # we have no other choice but removing unpaired surrogates
    text = re.sub(RE_SURROGATE_CHARACTER, "", text)

    return text, len(binary)


def utf8_surrogate_fix_search_function(encoding_name: str) -> codecs.CodecInfo:
    return codecs.CodecInfo(
        utf8_encode, utf8_surrogate_fix_decode, name="utf8-surrogate-fix"  # type: ignore
    )


codecs.register(utf8_surrogate_fix_search_function)


class ForceStrictModePool(QueuePool):
    pass


# My good old friend Enrico to the rescue:
# http://www.enricozini.org/2012/tips/sa-sqlmode-traditional/
#
# We set sql-mode=traditional on the server side as well, but enforce at the
# application level to be extra safe.
#
# Without this, MySQL will silently insert invalid values in the database if
@event.listens_for(ForceStrictModePool, "connect")
def receive_connect(dbapi_connection, connection_record):
    cur = dbapi_connection.cursor()
    cur.execute(
        "SET SESSION sql_mode='STRICT_TRANS_TABLES,STRICT_ALL_TABLES,"
        "NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,"
        "NO_ENGINE_SUBSTITUTION'"
    )
    cur = None

    assert dbapi_connection.encoding == "utf8"
    dbapi_connection.encoding = "utf8-surrogate-fix"


def safer_yield_per(query, id_field, start_id, count):
    """Incautious execution of 'for result in query.yield_per(N):' may cause
    slowness or OOMing over large tables. This is a less general but less
    dangerous alternative.

    Parameters
    ----------
    query: sqlalchemy.Query
        The query to yield windowed results from.
    id_field: A SQLAlchemy attribute to use for windowing. E.g.,
        `Transaction.id`
    start_id: The value of id_field at which to start iterating.
    count: int
        The number of results to fetch at a time.
    """
    cur_id = start_id
    while True:
        results = query.filter(id_field >= cur_id).order_by(id_field).limit(count).all()
        yield from results
        cur_id = results[-1].id + 1

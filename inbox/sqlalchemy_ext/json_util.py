# Copyright 2009-2015 MongoDB, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tools for using Python's :mod:`json` module with BSON documents.

This module provides two helper methods `dumps` and `loads` that wrap the
native :mod:`json` methods and provide explicit BSON conversion to and from
json.  This allows for specialized encoding and decoding of BSON documents
into `Mongo Extended JSON
<http://www.mongodb.org/display/DOCS/Mongo+Extended+JSON>`_'s *Strict*
mode.  This lets you encode / decode BSON documents to JSON even when
they use special BSON types.

Example usage (serialization):

.. doctest::

   >>> from bson import Binary, Code
   >>> from bson.json_util import dumps
   >>> dumps([{'foo': [1, 2]},
   ...        {'bar': {'hello': 'world'}},
   ...        {'code': Code("function x() { return 1; }")},
   ...        {'bin': Binary("\x01\x02\x03\x04")}])
   '[{"foo": [1, 2]}, {"bar": {"hello": "world"}}, {"code": {"$code": "function x() { return 1; }", "$scope": {}}}, {"bin": {"$binary": "AQIDBA==", "$type": "00"}}]'

Example usage (deserialization):

.. doctest::

   >>> from bson.json_util import loads
   >>> loads('[{"foo": [1, 2]}, {"bar": {"hello": "world"}}, {"code": {"$scope": {}, "$code": "function x() { return 1; }"}}, {"bin": {"$type": "00", "$binary": "AQIDBA=="}}]')
   [{u'foo': [1, 2]}, {u'bar': {u'hello': u'world'}}, {u'code': Code('function x() { return 1; }', {})}, {u'bin': Binary('...', 0)}]

Alternatively, you can manually pass the `default` to :func:`json.dumps`.
It won't handle :class:`~bson.binary.Binary` and :class:`~bson.code.Code`
instances (as they are extended strings you can't provide custom defaults),
but it will be faster as there is less recursion.

.. versionchanged:: 2.8
   The output format for :class:`~bson.timestamp.Timestamp` has changed from
   '{"t": <int>, "i": <int>}' to '{"$timestamp": {"t": <int>, "i": <int>}}'.
   This new format will be decoded to an instance of
   :class:`~bson.timestamp.Timestamp`. The old format will continue to be
   decoded to a python dict as before. Encoding to the old format is no longer
   supported as it was never correct and loses type information.
   Added support for $numberLong and $undefined - new in MongoDB 2.6 - and
   parsing $date in ISO-8601 format.

.. versionchanged:: 2.7
   Preserves order when rendering SON, Timestamp, Code, Binary, and DBRef
   instances.

.. versionchanged:: 2.3
   Added dumps and loads helpers to automatically handle conversion to and
   from json and supports :class:`~bson.binary.Binary` and
   :class:`~bson.code.Code`
"""

import calendar
import datetime
import json

EPOCH_NAIVE = datetime.datetime.utcfromtimestamp(0)


def dumps(obj, *args, **kwargs):
    """Helper function that wraps :class:`json.dumps`.

    Recursive function that handles all BSON types including
    :class:`~bson.binary.Binary` and :class:`~bson.code.Code`.

    .. versionchanged:: 2.7
       Preserves order when rendering SON, Timestamp, Code, Binary, and DBRef
       instances.
    """
    return json.dumps(_json_convert(obj), *args, **kwargs)


def loads(s, *args, **kwargs):
    """Helper function that wraps :class:`json.loads`.

    Automatically passes the object_hook for BSON type conversion.
    """
    kwargs["object_hook"] = lambda dct: object_hook(dct)
    return json.loads(s, *args, **kwargs)


def _json_convert(obj):
    """Recursive helper method that converts BSON types so they can be
    converted into json.
    """
    if hasattr(obj, "items"):
        return dict(((k, _json_convert(v)) for k, v in obj.items()))
    elif hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes)):
        return list((_json_convert(v) for v in obj))
    try:
        return default(obj)
    except TypeError:
        return obj


def object_hook(dct):
    if "$date" in dct:
        dtm = dct["$date"]
        secs = float(dtm) / 1000.0
        return EPOCH_NAIVE + datetime.timedelta(seconds=secs)
    return dct


def default(obj):
    # We preserve key order when rendering SON, DBRef, etc. as JSON by
    # returning a SON for those types instead of a dict.
    if isinstance(obj, datetime.datetime):
        # TODO share this code w/ bson.py?
        if obj.utcoffset() is not None:
            obj = obj - obj.utcoffset()
        millis = int(calendar.timegm(obj.timetuple()) * 1000 + obj.microsecond / 1000)
        return {"$date": millis}
    raise TypeError(f"{obj!r} is not JSON serializable")

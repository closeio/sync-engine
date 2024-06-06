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
native :mod:`json` methods and provide explicit datetime.datetime conversion to and from
json.  This is a very stripped version of `Mongo Extended JSON
<http://www.mongodb.org/display/DOCS/Mongo+Extended+JSON>`_'s *Strict*
mode handling just datetime.dateime fields because we don't serialize any
other types that are not handled by JSON.

The original unstripped version of this module can be found at
https://github.com/mongodb/mongo-python-driver/blob/18328a909545ece6e1cd7e172e28271a59e367d5/bson/json_util.py.
"""

import calendar
import datetime
import json

EPOCH_NAIVE = datetime.datetime.utcfromtimestamp(0)


def dumps(obj, *args, **kwargs):
    """Helper function that wraps :class:`json.dumps`.

    Recursive function that handles all datetime.datetime type.
    """
    return json.dumps(_json_convert(obj), *args, **kwargs)


def loads(s, *args, **kwargs):
    """Helper function that wraps :class:`json.loads`."""
    kwargs["object_hook"] = lambda dct: object_hook(dct)
    return json.loads(s, *args, **kwargs)


def _json_convert(obj):
    """Recursive helper method that converts datetime.datetime type so it can be
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
    if isinstance(obj, datetime.datetime):
        if obj.utcoffset() is not None:
            obj = obj - obj.utcoffset()
        millis = int(calendar.timegm(obj.timetuple()) * 1000 + obj.microsecond / 1000)
        return {"$date": millis}
    raise TypeError(f"{obj!r} is not JSON serializable")

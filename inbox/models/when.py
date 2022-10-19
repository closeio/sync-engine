import contextlib
from typing import List, Type, Union

import arrow


def parse_as_when(raw):
    """
    Tries to parse a dictionary into a corresponding Date, DateSpan,
    Time, or TimeSpan instance.

    Raises
    -------
    ValueError

    """
    when_classes = [TimeSpan, Time, DateSpan, Date]
    keys_for_type = {tuple(sorted(cls_.json_keys)): cls_ for cls_ in when_classes}
    given_keys = tuple(sorted(set(raw.keys()) - set("object")))
    when_type = keys_for_type.get(given_keys)
    if when_type is None:
        raise ValueError("When object had invalid keys.")
    return when_type.parse(raw)


def parse_utc(datetime):
    # type: (Union[float, int, str, arrow.Arrow]) -> arrow.Arrow
    # Arrow can handle epoch timestamps as well as most ISO-8601 strings
    with contextlib.suppress(ValueError, TypeError):
        datetime = float(datetime)

    return arrow.get(datetime).to("utc")


class When:
    """
    Abstract class which can represent a moment in time or a span between
        two moments. Initialize one of its subclasses `Time`, `TimeSpan`,
        `Date` or `DateSpan` to concretely define which type you need.

    Args:
        start (datetime): Starting time
        end (datetime, optional): End time. If missing, start will be used.

    """

    json_keys: List[str]
    all_day = False
    spanning = False

    @classmethod
    def parse(cls, raw):
        parsed_times = cls.parse_keys(raw)
        return cls(*parsed_times)

    @classmethod
    def parse_keys(cls, raw):
        times = []
        for key in cls.json_keys:
            try:
                time = parse_utc(raw[key])
                times.append(time)
            except (AttributeError, ValueError, TypeError):
                raise ValueError(f"'{key}' parameter invalid.")
        return times

    def __init__(self, start, end=None):
        self.start = start
        self.end = end or start

    def __repr__(self):
        return f"{type(self)} ({self.start} - {self.end})"

    @property
    def is_time(self):
        return isinstance(self, Time)

    @property
    def is_date(self):
        return isinstance(self, Date)

    @property
    def delta(self):
        return self.end - self.start

    def get_time_dict(self):
        times = (self.start, self.end)
        return dict(zip(self.json_keys, times))


class AllDayWhen(When):
    all_day = True


class SpanningWhen(When):
    spanning = True
    singular_cls: Type

    @classmethod
    def parse(cls, raw):
        # If initializing a span, we sanity check the timestamps and initialize
        # the singular form if they are equal.
        start, end = cls.parse_keys(raw)
        if start > end:
            raise ValueError("'{}' must be < '{}'.".format(*cls.json_keys))
        if start == end:
            return cls.singular_cls(start)
        return cls(start, end)


class Time(When):
    json_keys = ["time"]


class TimeSpan(Time, SpanningWhen):
    json_keys = ["start_time", "end_time"]
    singular_cls = Time


class Date(AllDayWhen):
    json_keys = ["date"]


class DateSpan(Date, AllDayWhen, SpanningWhen):
    json_keys = ["start_date", "end_date"]
    singular_cls = Date

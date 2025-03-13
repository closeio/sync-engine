import contextlib
import datetime
from typing import Any, Union

import arrow  # type: ignore[import-untyped]


def parse_as_when(
    raw: dict[str, Any]
) -> Union["TimeSpan", "Time", "DateSpan", "Date"]:
    """
    Tries to parse a dictionary into a corresponding Date, DateSpan,
    Time, or TimeSpan instance.

    Raises
    ------
    ValueError

    """  # noqa: D401
    when_classes = [TimeSpan, Time, DateSpan, Date]
    keys_for_type = {
        tuple(sorted(cls_.json_keys)): cls_ for cls_ in when_classes
    }
    given_keys = tuple(sorted(set(raw.keys()) - set("object")))
    when_type = keys_for_type.get(given_keys)
    if when_type is None:
        raise ValueError("When object had invalid keys.")
    return when_type.parse(raw)


def parse_utc(datetime: float | int | str | arrow.Arrow) -> arrow.Arrow:
    # Arrow can handle epoch timestamps as well as most ISO-8601 strings
    with contextlib.suppress(ValueError, TypeError):
        datetime = float(datetime)

    return arrow.get(datetime).to("utc")


class When:
    """
    Represent a moment in time or a span between
    two moments. Initialize one of its subclasses `Time`, `TimeSpan`,
    `Date` or `DateSpan` to concretely define which type you need.
    """

    json_keys: list[str]
    all_day = False
    spanning = False

    @classmethod
    def parse(cls, raw: dict[str, Any]):  # type: ignore[no-untyped-def]  # noqa: ANN206
        parsed_times = cls.parse_keys(raw)
        return cls(*parsed_times)

    @classmethod
    def parse_keys(cls, raw: dict[str, Any]) -> list[arrow.Arrow]:
        times = []
        for key in cls.json_keys:
            try:
                time = parse_utc(raw[key])
                times.append(time)
            except (AttributeError, ValueError, TypeError):
                raise ValueError(f"'{key}' parameter invalid.")  # noqa: B904
        return times

    def __init__(
        self, start: arrow.Arrow, end: arrow.Arrow | None = None
    ) -> None:
        self.start = start
        self.end = end or start

    def __repr__(self) -> str:
        return f"{type(self)} ({self.start} - {self.end})"

    @property
    def is_time(self) -> bool:
        return isinstance(self, Time)

    @property
    def is_date(self) -> bool:
        return isinstance(self, Date)

    @property
    def delta(self) -> datetime.timedelta:
        return self.end - self.start

    def get_time_dict(self) -> dict[str, arrow.Arrow]:
        times = (self.start, self.end)
        return dict(zip(self.json_keys, times))


class SpanningWhen(When):
    spanning = True
    singular_cls: type

    @classmethod
    def parse(cls, raw: dict[str, Any]):  # type: ignore[no-untyped-def]  # noqa: ANN206
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


class Date(When):
    json_keys = ["date"]
    all_day = True


class DateSpan(Date, SpanningWhen):
    json_keys = ["start_date", "end_date"]
    singular_cls = Date
    all_day = True

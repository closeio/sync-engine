import datetime
import time
from typing import Any, Callable, Dict, Iterable, Optional

import pytz
import requests

BASE_URL = "https://graph.microsoft.com/v1.0"


class MicrosoftGraphClient:
    def __init__(self, get_token: Callable[[], str]):
        self._get_token = get_token

    def request(
        self,
        method: str,
        resource_url: str,
        *,
        params: Optional[Dict[str, str]] = None,
        json: Optional[Dict[str, str]] = None,
        max_retries=10,
    ) -> Dict[str, Any]:
        assert resource_url.startswith("/")
        resource_url = BASE_URL + resource_url

        headers = {"Authorization": "Bearer " + self._get_token()}

        retry = 0
        while retry < max_retries:
            response = requests.request(
                method, resource_url, params=params, headers=headers, json=json
            )
            if response.status_code == 429:
                sleep_seconds = int(response.headers.get("Retry-After", 10))
                time.sleep(sleep_seconds)
                retry += 1
                continue
            elif response.status_code == 401:
                raise NotImplementedError()  # TODO
            break

        response.raise_for_status()

        return response.json()

    def _iter(
        self, initial_url: str, *, params: Optional[Dict[str, str]] = None
    ) -> Iterable[Dict[str, Any]]:
        next_url: Optional[str] = initial_url
        while next_url:
            response = self.request("GET", next_url, params=params)
            yield from response["value"]
            next_url = response.get("@odata.nextLink")

    def iter_calendars(self) -> Iterable[Dict[str, Any]]:
        yield from self._iter("/me/calendars")

    def get_calendar(self, calendar_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/me/calendars/{calendar_id}")

    def iter_events(self, calendar_id: str) -> Iterable[Dict[str, Any]]:
        yield from self._iter(f"/me/calendars/{calendar_id}/events")

    def get_event(self, event_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/me/events/{event_id}")

    def iter_event_instances(
        self, event_id: str, start: datetime.datetime, end: datetime.datetime
    ) -> Iterable[Dict[str, Any]]:
        assert start.tzinfo == pytz.UTC
        assert end.tzinfo == pytz.UTC
        return self._iter(
            f"/me/events/{event_id}/instances",
            params={"startDateTime": start.isoformat(), "endDateTime": end.isoformat()},
        )

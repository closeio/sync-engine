import contextlib
import datetime
import time
from typing import Any, Callable, Dict, Iterable, Literal, Optional

import pytz
import requests

BASE_URL = "https://graph.microsoft.com/v1.0"


ChangeType = Iterable[Literal["updated", "created", "deleted"]]


class MicrosoftGraphClientException(Exception):
    def __init__(self, response: requests.Response):
        args = []
        with contextlib.suppress(Exception):
            error = response.json()["error"]
            code = error["code"]
            args.append(code)
            message = error["message"]
            args.append(message)

        super().__init__(*args)
        self.response = response


class MicrosoftGraphClient:
    def __init__(self, get_token: Callable[[], str]):
        self._get_token = get_token
        # Session lets us use HTTP connection pooling
        # lowering the number of TCP connections needed
        # to make many requests
        self._session = requests.Session()

    def request(
        self,
        method: str,
        resource_url: str,
        *,
        params: Optional[Dict[str, str]] = None,
        json: Optional[Dict[str, str]] = None,
        max_retries=10,
        timeout=10,
    ) -> Dict[str, Any]:
        if not resource_url.startswith(BASE_URL):
            assert resource_url.startswith("/")
            resource_url = BASE_URL + resource_url

        headers = {"Authorization": "Bearer " + self._get_token()}

        retry = 0
        while retry < max_retries:
            response = self._session.request(
                method,
                resource_url,
                params=params,
                headers=headers,
                json=json,
                timeout=timeout,
            )
            if response.status_code == 429:
                sleep_seconds = int(response.headers.get("Retry-After", 5))
                time.sleep(sleep_seconds)
                retry += 1
                continue
            break

        try:
            response.raise_for_status()
        except requests.HTTPError as e:
            raise MicrosoftGraphClientException(response) from e

        if not response.text:
            # Some DELETE operations return empty body
            return {}
        else:
            return response.json()

    def _iter(
        self, initial_url: str, *, params: Optional[Dict[str, str]] = None
    ) -> Iterable[Dict[str, Any]]:
        next_url: Optional[str] = initial_url
        while next_url:
            response = self.request("GET", next_url, params=params)
            yield from response["value"]
            next_url = response.get("@odata.nextLink")
            params = None

    def iter_calendars(self) -> Iterable[Dict[str, Any]]:
        yield from self._iter("/me/calendars")

    def get_calendar(self, calendar_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/me/calendars/{calendar_id}")

    def iter_events(
        self, calendar_id: str, *, modified_after: Optional[datetime.datetime] = None
    ) -> Iterable[Dict[str, Any]]:
        if modified_after:
            assert modified_after.tzinfo == pytz.UTC
            params = {
                "$filter": f"lastModifiedDateTime gt {modified_after.replace(tzinfo=None).isoformat()}Z"
            }
        else:
            params = None
        yield from self._iter(f"/me/calendars/{calendar_id}/events", params=params)

    def get_event(self, event_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/me/events/{event_id}")

    def iter_event_instances(
        self, event_id: str, *, start: datetime.datetime, end: datetime.datetime
    ) -> Iterable[Dict[str, Any]]:
        assert start.tzinfo == pytz.UTC
        assert end.tzinfo == pytz.UTC
        assert end >= start
        yield from self._iter(
            f"/me/events/{event_id}/instances",
            params={"startDateTime": start.isoformat(), "endDateTime": end.isoformat()},
        )

    def iter_subscriptions(self) -> Iterable[Dict[str, Any]]:
        yield from self._iter("/subscriptions")

    def get_subscription(self, subscription_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/subscriptions/{subscription_id}")

    def subscribe(
        self,
        *,
        resource_url: str,
        change_type: ChangeType,
        webhook_url: str,
        secret: str,
        expiration: Optional[datetime.datetime] = None,
    ) -> Dict[str, Any]:
        assert resource_url.startswith("/")

        if not expiration:
            expiration = datetime.datetime.now(tz=pytz.UTC) + datetime.timedelta(
                minutes=4230
            )
        assert expiration.tzinfo == pytz.UTC

        json = {
            "changeType": ",".join(change_type),
            "notificationUrl": webhook_url,
            "resource": resource_url,
            "expirationDateTime": expiration.isoformat(),
            "clientState": secret,
        }

        return self.request("POST", "/subscriptions", json=json)

    def unsubscribe(self, subscription_id: str) -> Dict[str, Any]:
        return self.request("DELETE", f"/subscriptions/{subscription_id}")

    def renew_subscription(
        self, subscription_id: str, expiration: Optional[datetime.datetime] = None
    ) -> Dict[str, Any]:
        if not expiration:
            expiration = datetime.datetime.now(tz=pytz.UTC) + datetime.timedelta(
                minutes=4230
            )
        assert expiration.tzinfo == pytz.UTC

        json = {
            "expirationDateTime": expiration.isoformat(),
        }

        return self.request("PATCH", f"/subscriptions/{subscription_id}", json=json)

    def subscribe_to_calendar_changes(
        self, *, webhook_url: str, secret: str
    ) -> Dict[str, Any]:
        return self.subscribe(
            resource_url="/me/calendars",
            change_type=["updated", "deleted"],
            webhook_url=webhook_url,
            secret=secret,
        )

    def subscribe_to_event_changes(
        self, calendar_id, *, webhook_url: str, secret: str
    ) -> Dict[str, Any]:
        return self.subscribe(
            resource_url=f"/me/calendars/{calendar_id}/events",
            change_type=["created", "updated", "deleted"],
            webhook_url=webhook_url,
            secret=secret,
        )

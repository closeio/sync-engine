import contextlib
import datetime
import enum
import time
from typing import Any, Callable, Dict, Iterable, Optional

import pytz
import requests

BASE_URL = "https://graph.microsoft.com/v1.0"


class ChangeType(enum.Enum):
    UPDATED = "updated"
    CREATED = "created"
    DELETED = "deleted"


class MicrosoftGraphClientException(Exception):
    def __init__(self, response: requests.Response):
        """
        Arguments:
            response: The reponse that caused exception
        """
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
    """
    Provide basic operations to read Outlook calendar
    over Microsoft Graph API.
    """

    def __init__(self, get_token: Callable[[], str]):
        """
        Arguments:
            get_token: Function that returns user token
        """
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
        """
        Perform request.

        Automatically recover from 429 Too Many Requests.

        Arguments:
            method: HTTP method
            resource_url: Either full URL or part after version e.g /me/calendars
            params: GET parameters
            json: JSON to POST or PATCH
            max_retries: The number of maximum retires for 429
            timeout: HTTP request timeout
        """
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
        """
        Lazily iterate over paged resources.

        Arguments:
            initial_url: The initial resource url
            params: GET params
        """
        next_url: Optional[str] = initial_url
        while next_url:
            response = self.request("GET", next_url, params=params)
            yield from response["value"]
            next_url = response.get("@odata.nextLink")
            # @odata.nextLink will already incorporate params
            params = None

    def iter_calendars(self) -> Iterable[Dict[str, Any]]:
        """
        Lazily iterate user calendars.
        """

        # TODO: Figure out the top limit we can use on this endpoint
        yield from self._iter("/me/calendars")

    def get_calendar(self, calendar_id: str) -> Dict[str, Any]:
        """
        Get a single calendar.

        Arguments:
            calendar_id: The calendar id
        """
        return self.request("GET", f"/me/calendars/{calendar_id}")

    def iter_events(
        self, calendar_id: str, *, modified_after: Optional[datetime.datetime] = None
    ) -> Iterable[Dict[str, Any]]:
        """
        Lazily iterate events in a calendar.

        Arguments:
            calendar_id: The calendar id
            modified_after: If specified only return events given datetime
        """
        if modified_after:
            assert modified_after.tzinfo == pytz.UTC
            params = {
                "$filter": f"lastModifiedDateTime gt {modified_after.replace(tzinfo=None).isoformat()}Z"
            }
        else:
            params = None
        # TODO: Figure out the top limit we can use on this endpoint
        yield from self._iter(f"/me/calendars/{calendar_id}/events", params=params)

    def get_event(self, event_id: str) -> Dict[str, Any]:
        """
        Get a single event.

        Arguments:
            event_id: The event id
        """
        return self.request("GET", f"/me/events/{event_id}")

    def iter_event_instances(
        self, event_id: str, *, start: datetime.datetime, end: datetime.datetime
    ) -> Iterable[Dict[str, Any]]:
        """
        Expand series master instances.

        Arguments:
            event_id: The event id
            start: the start of the period
            end: the end of the period
        """
        assert start.tzinfo == pytz.UTC
        assert end.tzinfo == pytz.UTC
        assert end >= start
        yield from self._iter(
            f"/me/events/{event_id}/instances",
            params={
                "startDateTime": start.isoformat(),
                "endDateTime": end.isoformat(),
                # The default amount of instances per page is 10,
                # as we want to do the least
                # amount of requests possible we raise it to 500.
                "top": "500",
            },
        )

    def iter_subscriptions(self) -> Iterable[Dict[str, Any]]:
        """
        Lazily iterate subscriptions.
        """
        yield from self._iter("/subscriptions")

    def get_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """
        Get a single subscription.

        Arguments:
            subscription_id: The subscription id
        """
        return self.request("GET", f"/subscriptions/{subscription_id}")

    def subscribe(
        self,
        *,
        resource_url: str,
        change_types: Iterable[ChangeType],
        webhook_url: str,
        secret: str,
        expiration: Optional[datetime.datetime] = None,
    ) -> Dict[str, Any]:
        """
        Subsribe to resource changes.

        Arguments:
            resource_url: The resource URL we want to subscribe to
            change_types: Types of changes we want to listen to
            webhook_url: The webhook URL that should receive changes
            secret: Secret that can be used to verify authenticity of
                date arriving on a webhook
            expiration: The date that subscription will expire
        """

        if resource_url.startswith(BASE_URL):
            resource_url = resource_url[len(BASE_URL) :]

        assert resource_url.startswith("/")

        if not expiration:
            # The maximum expiration for a webhook subscription
            # is 4230 minutes, which is slightly less than 3 days.
            expiration = datetime.datetime.now(tz=pytz.UTC) + datetime.timedelta(
                minutes=4230
            )
        assert expiration.tzinfo == pytz.UTC

        json = {
            "changeType": ",".join(change_type.value for change_type in change_types),
            "notificationUrl": webhook_url,
            "resource": resource_url,
            "expirationDateTime": expiration.isoformat(),
            "clientState": secret,
        }

        return self.request("POST", "/subscriptions", json=json)

    def unsubscribe(self, subscription_id: str) -> Dict[str, Any]:
        """
        Unsubscribe from resource changes.

        Arguments:
            subscription_id: The subscription id
        """
        return self.request("DELETE", f"/subscriptions/{subscription_id}")

    def renew_subscription(
        self, subscription_id: str, expiration: Optional[datetime.datetime] = None
    ) -> Dict[str, Any]:
        """
        Renew a subscription before it expires.

        Arguments:
            subscription_id: The subscription id
            expiration: The new date that subscription will expire at
        """
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
        """
        Subscribe to calendar changes.

        Arguments:
            webhook_url: The webhook URL that should receive changes
            secret: Secret that can be used to verify authenticity of
                date arriving on a webhook
        """
        return self.subscribe(
            resource_url="/me/calendars",
            # Quirk: subscribing to "created" on calendars raises an API error.
            # Nonetheless calendar creations, updates and deletes are all delivered
            # as updates to webhooks.
            change_types=[ChangeType.UPDATED, ChangeType.DELETED],
            webhook_url=webhook_url,
            secret=secret,
        )

    def subscribe_to_event_changes(
        self, calendar_id: str, *, webhook_url: str, secret: str
    ) -> Dict[str, Any]:
        """
        Subscribe to event changes in a calendar.

        Arguments:
            calendar_id: The calendar id
            webhook_url: The webhook URL that should receive changes
            secret: Secret that can be used to verify authenticity of
                date arriving on a webhook
        """

        return self.subscribe(
            resource_url=f"/me/calendars/{calendar_id}/events",
            change_types=[ChangeType.CREATED, ChangeType.UPDATED, ChangeType.DELETED],
            webhook_url=webhook_url,
            secret=secret,
        )

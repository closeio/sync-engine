import contextlib
import datetime
import enum
import time
from typing import Any, Callable, Container, Dict, Iterable, Optional

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


def format_datetime(dt: datetime.datetime) -> str:
    """
    Format UTC datetime in format accepted by Microsoft Graph.

    Arguments:
        dt: the datetime

    Returns:
        Formatted datetime e.g. 2022-10-17T15:46:36.335288Z
    """
    assert dt.tzinfo == pytz.UTC

    return dt.replace(tzinfo=None).isoformat() + "Z"


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
        retry_on: Container[int] = frozenset({429, 503}),
        max_retries: int = 10,
        timeout: int = 20,
    ) -> Dict[str, Any]:
        """
        Perform request.

        Automatically recover from 429 Too Many Requests.

        Arguments:
            method: HTTP method
            resource_url: Either full URL or part after version e.g /me/calendars
            params: GET parameters
            json: JSON to POST or PATCH
            retry_on: List of HTTP status codes to automatically retry on
            max_retries: The number of maximum retires for 429
            timeout: HTTP request timeout in seconds
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
            if response.status_code in retry_on:
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

        https://learn.microsoft.com/en-us/graph/api/user-list-calendars

        Returns:
            Iterable of calendars.
            https://learn.microsoft.com/en-us/graph/api/resources/calendar
        """
        # TODO: Figure out the top limit we can use on this endpoint
        yield from self._iter("/me/calendars")

    def get_calendar(self, calendar_id: str) -> Dict[str, Any]:
        """
        Get a single calendar.

        https://learn.microsoft.com/en-us/graph/api/calendar-get

        Arguments:
            calendar_id: The calendar id

        Returns:
            The calendar.
            https://learn.microsoft.com/en-us/graph/api/resources/calendar
        """
        return self.request("GET", f"/me/calendars/{calendar_id}")

    def iter_events(
        self,
        calendar_id: str,
        *,
        modified_after: Optional[datetime.datetime] = None,
        fields: Optional[Iterable[str]] = None,
    ) -> Iterable[Dict[str, Any]]:
        """
        Lazily iterate events in a calendar.

        https://learn.microsoft.com/en-us/graph/api/user-list-events

        Arguments:
            calendar_id: The calendar id
            modified_after: If specified only return events given datetime

        Returns:
            Iterable of events.
            https://learn.microsoft.com/en-us/graph/api/resources/event
        """
        params = {
            # The default amount of events per page is 10,
            # as we want to do the least
            # amount of requests possible we raise it to 500.
            "top": "500",
        }

        if modified_after:
            assert modified_after.tzinfo == pytz.UTC
            params[
                "$filter"
            ] = f"lastModifiedDateTime gt {format_datetime(modified_after)}"
        if fields:
            params["$select"] = ",".join(fields)

        # TODO: Figure out the top limit we can use on this endpoint
        yield from self._iter(f"/me/calendars/{calendar_id}/events", params=params)

    def get_event(
        self, event_id: str, *, fields: Optional[Iterable[str]] = None
    ) -> Dict[str, Any]:
        """
        Get a single event.

        https://learn.microsoft.com/en-us/graph/api/event-get

        Arguments:
            event_id: The event id

        Returns:
            The event.
            https://learn.microsoft.com/en-us/graph/api/resources/event
        """

        params = {}
        if fields:
            params["$select"] = ",".join(fields)

        return self.request("GET", f"/me/events/{event_id}", params=params)

    def iter_event_instances(
        self,
        event_id: str,
        *,
        start: datetime.datetime,
        end: datetime.datetime,
        fields: Optional[Iterable[str]] = None,
    ) -> Iterable[Dict[str, Any]]:
        """
        Lazily expand series master instances.

        https://learn.microsoft.com/en-us/graph/api/event-list-instances

        Arguments:
            event_id: The event id
            start: the start of the period
            end: the end of the period

        Returns:
            Iterable of event instances.
            https://learn.microsoft.com/en-us/graph/api/resources/event
        """
        assert start.tzinfo == pytz.UTC
        assert end.tzinfo == pytz.UTC
        assert end >= start

        params = {
            "startDateTime": format_datetime(start),
            "endDateTime": format_datetime(end),
            # The default amount of instances per page is 10,
            # as we want to do the least
            # amount of requests possible we raise it to 500.
            "top": "500",
        }

        if fields:
            params["$select"] = ",".join(fields)

        yield from self._iter(
            f"/me/events/{event_id}/instances", params=params,
        )

    def iter_subscriptions(self) -> Iterable[Dict[str, Any]]:
        """
        Lazily iterate subscriptions.

        https://learn.microsoft.com/en-us/graph/api/subscription-list

        Returns:
            Iterable of subscriptions.
            https://learn.microsoft.com/en-us/graph/api/resources/subscription
        """
        yield from self._iter("/subscriptions")

    def get_subscription(self, subscription_id: str) -> Dict[str, Any]:
        """
        Get a single subscription.

        https://learn.microsoft.com/en-us/graph/api/subscription-get

        Arguments:
            subscription_id: The subscription id

        Returns:
            The subscription.
            https://learn.microsoft.com/en-us/graph/api/resources/subscription
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
        Subscribe to resource changes.

        https://learn.microsoft.com/en-us/graph/api/subscription-post-subscriptions

        Arguments:
            resource_url: The resource URL we want to subscribe to
            change_types: Types of changes we want to listen to
            webhook_url: The webhook URL that should receive changes
            secret: Secret that can be used to verify authenticity of
                date arriving on a webhook
            expiration: The date that subscription will expire

        Returns:
            The subscription.
            https://learn.microsoft.com/en-us/graph/api/resources/subscription
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
            "expirationDateTime": format_datetime(expiration),
            "clientState": secret,
        }

        max_retries = 5
        for _ in range(max_retries):
            try:
                return self.request("POST", "/subscriptions", json=json)
            except MicrosoftGraphClientException as e:
                last_exception = e
                message, description = e.args
                if message == "InvalidRequest" and description.startswith(
                    "The underlying connection was closed"
                ):
                    time.sleep(5)
                    continue

                raise

        last_exception.args = (*last_exception.args, "Max retries reached")
        raise last_exception

    def unsubscribe(self, subscription_id: str) -> Dict[str, Any]:
        """
        Unsubscribe from resource changes.

        https://learn.microsoft.com/en-us/graph/api/subscription-delete

        Arguments:
            subscription_id: The subscription id

        Returns:
            Empty dict.
        """
        return self.request("DELETE", f"/subscriptions/{subscription_id}")

    def renew_subscription(
        self, subscription_id: str, expiration: Optional[datetime.datetime] = None
    ) -> Dict[str, Any]:
        """
        Renew a subscription before it expires.

        https://learn.microsoft.com/en-us/graph/api/subscription-update

        Arguments:
            subscription_id: The subscription id
            expiration: The new date that subscription will expire at

        Returns:
            The subscription.
            https://learn.microsoft.com/en-us/graph/api/resources/subscription
        """
        if not expiration:
            expiration = datetime.datetime.now(tz=pytz.UTC) + datetime.timedelta(
                minutes=4230
            )
        assert expiration.tzinfo == pytz.UTC

        json = {
            "expirationDateTime": format_datetime(expiration),
        }

        return self.request("PATCH", f"/subscriptions/{subscription_id}", json=json)

    def subscribe_to_calendar_changes(
        self, *, webhook_url: str, secret: str
    ) -> Dict[str, Any]:
        """
        Subscribe to calendar changes.

        https://learn.microsoft.com/en-us/graph/api/subscription-post-subscriptions

        Arguments:
            webhook_url: The webhook URL that should receive changes
            secret: Secret that can be used to verify authenticity of
                date arriving on a webhook

        Returns:
            The subscription.
            https://learn.microsoft.com/en-us/graph/api/resources/subscription
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

        https://learn.microsoft.com/en-us/graph/api/subscription-post-subscriptions

        Arguments:
            calendar_id: The calendar id
            webhook_url: The webhook URL that should receive changes
            secret: Secret that can be used to verify authenticity of
                date arriving on a webhook

        Returns:
            The subscription.
            https://learn.microsoft.com/en-us/graph/api/resources/subscription
        """

        return self.subscribe(
            resource_url=f"/me/calendars/{calendar_id}/events",
            change_types=[ChangeType.CREATED, ChangeType.UPDATED, ChangeType.DELETED],
            webhook_url=webhook_url,
            secret=secret,
        )

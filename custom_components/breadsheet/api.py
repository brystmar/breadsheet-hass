"""A thin async client for breadsheet's /api/v2.

Only what the integration uses. The contract is documented in the breadsheet repo at docs/api-v2.md;
branch on error `code`, never on `message`, and ignore response fields this client doesn't know.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class BreadsheetError(Exception):
    """Base class for every error this client raises."""


class BreadsheetConnectionError(BreadsheetError):
    """Breadsheet couldn't be reached or answered with a 5xx: worth retrying, after `retry_after` seconds if set."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class BreadsheetApiError(BreadsheetError):
    """Breadsheet refused the request with a JSON error body (any 4xx)."""

    def __init__(self, status: int, code: str, message: str, retry_after: int | None = None) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after


class BreadsheetAuthError(BreadsheetApiError):
    """The device token is unknown or was revoked (401 `token_invalid`): the user has to pair again."""


@dataclass(frozen=True, slots=True)
class Pairing:
    """What `POST /api/v2/devices` returns. The token is shown this once and never again."""

    token: str
    connection_id: str
    user_id: str
    username: str


@dataclass(frozen=True, slots=True)
class BakeList:
    """One poll of the active Fancy bakes. `bakes` is None when nothing changed since `etag` (a 304)."""

    bakes: list[dict[str, Any]] | None
    etag: str | None
    poll_interval: int | None


@dataclass(frozen=True, slots=True)
class _Response:
    status: int
    headers: Mapping[str, str]
    body: dict[str, Any]


def _poll_interval(headers: Mapping[str, str]) -> int | None:
    """The server's suggested seconds until the next poll (X-Breadsheet-Poll-Interval), if readable."""
    try:
        value = int(headers.get("X-Breadsheet-Poll-Interval", ""))
    except ValueError:
        return None
    return value if value > 0 else None


def _retry_after(response: aiohttp.ClientResponse) -> int | None:
    """`Retry-After` in seconds, if the server sent one we can read. (Breadsheet sends seconds, never a date.)"""
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0, int(value))
    except ValueError:
        return None


class BreadsheetClient:
    """Calls breadsheet's /api/v2, as the paired device when it has a token."""

    def __init__(self, session: aiohttp.ClientSession, api_url: str, token: str | None = None,
                 user_agent: str = "breadsheet-hass") -> None:
        self._session = session
        self._base = api_url.rstrip("/") + "/api/v2"
        self._token = token
        self._user_agent = user_agent

    async def pair(self, code: str, label: str) -> Pairing:
        """Exchange a pairing code from breadsheet.com for a device token. Codes are single use."""
        body = await self._request("POST", "/devices", json={"code": code, "label": label}, authenticated=False)
        try:
            return Pairing(token=body["token"], connection_id=body["connection"]["id"],
                           user_id=body["user"]["id"], username=body["user"]["username"])
        except (KeyError, TypeError) as err:
            raise BreadsheetError("Breadsheet's pairing response was missing fields") from err

    async def active_fancy_bakes(self, etag: str | None = None) -> BakeList:
        """
        The active Fancy bakes, the only ones HA tracks. Sends `If-None-Match` so an unchanged list is a cheap 304;
        idle polls like this are answered without waking breadsheet's database.
        """
        response = await self._send("GET", "/bakes?status=active&alert=fancy",
                                    headers={"If-None-Match": etag} if etag else None)
        interval = _poll_interval(response.headers)
        new_etag = response.headers.get("ETag") or etag
        if response.status == HTTPStatus.NOT_MODIFIED:
            return BakeList(bakes=None, etag=new_etag, poll_interval=interval)
        bakes = response.body.get("bakes")
        if not isinstance(bakes, list) or not all(isinstance(bake, dict) for bake in bakes):
            raise BreadsheetConnectionError("Breadsheet's bake list was missing or malformed")
        return BakeList(bakes=bakes, etag=new_etag, poll_interval=interval)

    async def _request(self, method: str, path: str, *, json: dict[str, Any] | None = None,
                       authenticated: bool = True) -> dict[str, Any]:
        return (await self._send(method, path, json=json, authenticated=authenticated)).body

    async def _send(self, method: str, path: str, *, json: dict[str, Any] | None = None,
                    headers: Mapping[str, str] | None = None, authenticated: bool = True) -> _Response:
        headers = {**(headers or {}),
            "Accept": "application/json",
            "User-Agent": self._user_agent,
            # Metrics only: counts this as a call from HA (agent tools send `agent` instead, from M4)
            "X-Breadsheet-Via": "ha",
        }
        if authenticated and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if method != "GET":
            # Breadsheet refuses state-changing requests without a JSON content type (its CSRF guard)
            json = {} if json is None else json

        try:
            async with self._session.request(method, self._base + path, json=json, headers=headers,
                                             timeout=REQUEST_TIMEOUT) as response:
                if response.status in (HTTPStatus.NO_CONTENT, HTTPStatus.NOT_MODIFIED):
                    return _Response(response.status, response.headers, {})
                if response.status >= HTTPStatus.INTERNAL_SERVER_ERROR:
                    # 503 `service_unavailable` comes with Retry-After; App Engine's own errors may not be JSON
                    raise BreadsheetConnectionError(f"Breadsheet answered {response.status}",
                                                    retry_after=_retry_after(response))
                try:
                    body = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError) as err:
                    raise BreadsheetConnectionError(f"Breadsheet answered {response.status} without JSON") from err
                if not isinstance(body, dict):
                    raise BreadsheetConnectionError(f"Breadsheet answered {response.status} with unexpected JSON")
                if response.status >= HTTPStatus.BAD_REQUEST:
                    code = str(body.get("code") or "http_error")
                    message = str(body.get("message") or "")
                    error = BreadsheetAuthError if code == "token_invalid" else BreadsheetApiError
                    raise error(response.status, code, message, retry_after=_retry_after(response))
                return _Response(response.status, response.headers, body)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise BreadsheetConnectionError(f"Couldn't reach breadsheet: {err.__class__.__name__}") from err

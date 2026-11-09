"""Tests for the /api/v2 client's error mapping, beyond what the config flow exercises."""

from http import HTTPStatus

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from custom_components.breadsheet.api import (BreadsheetApiError, BreadsheetAuthError, BreadsheetClient,
                                              BreadsheetConnectionError)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

URL = "https://api.example.test/api/v2/bakes"


@pytest.fixture
async def client(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> BreadsheetClient:
    """Created after aioclient_mock, which has to patch HA's client session before it's made."""
    return BreadsheetClient(async_get_clientsession(hass), "https://api.example.test/", "secret")


async def test_calls_send_the_token_and_json_on_writes(client: BreadsheetClient,
                                                       aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(URL, status=HTTPStatus.NO_CONTENT)
    assert await client._request("POST", "/bakes") == {}
    _, _, body, headers = aioclient_mock.mock_calls[0]
    assert body == {}  # an empty JSON body, so breadsheet's JSON-only write guard lets it through
    assert headers["Authorization"] == "Bearer secret"


async def test_a_revoked_token_is_an_auth_error(client: BreadsheetClient,
                                                aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(URL, status=HTTPStatus.UNAUTHORIZED, json={"code": "token_invalid", "message": "Pair again."})
    with pytest.raises(BreadsheetAuthError) as caught:
        await client._request("GET", "/bakes")
    assert caught.value.code == "token_invalid"


@pytest.mark.parametrize(("header", "expected"), [("120", 120), ("soon", None), ("-5", 0)])
async def test_retry_after_is_read_as_seconds(client: BreadsheetClient, aioclient_mock: AiohttpClientMocker,
                                              header: str, expected: int | None) -> None:
    aioclient_mock.get(URL, status=HTTPStatus.TOO_MANY_REQUESTS, json={"code": "rate_limited", "message": ""},
                       headers={"Retry-After": header})
    with pytest.raises(BreadsheetApiError) as caught:
        await client._request("GET", "/bakes")
    assert caught.value.retry_after == expected


@pytest.mark.parametrize("response", [{"text": "not json"}, {"json": ["a", "list"]}])
async def test_unexpected_bodies_are_worth_retrying(client: BreadsheetClient, aioclient_mock: AiohttpClientMocker,
                                                    response: dict) -> None:
    aioclient_mock.get(URL, status=HTTPStatus.OK, **response)
    with pytest.raises(BreadsheetConnectionError):
        await client._request("GET", "/bakes")


async def test_an_error_without_a_code_is_http_error(client: BreadsheetClient,
                                                     aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(URL, status=HTTPStatus.NOT_FOUND, json={})
    with pytest.raises(BreadsheetApiError) as caught:
        await client._request("GET", "/bakes")
    assert caught.value.code == "http_error"


@pytest.mark.parametrize("body", [{}, {"bakes": {"not": "a list"}}, {"bakes": ["not a bake"]}])
async def test_a_malformed_bake_list_is_worth_retrying(client: BreadsheetClient, aioclient_mock: AiohttpClientMocker,
                                                       body: dict) -> None:
    aioclient_mock.get("https://api.example.test/api/v2/bakes?status=active&alert=fancy", json=body)
    with pytest.raises(BreadsheetConnectionError):
        await client.active_fancy_bakes()

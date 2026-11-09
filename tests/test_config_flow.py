"""Tests for the pairing config flow."""

from http import HTTPStatus

import aiohttp
import pytest

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.breadsheet.const import (CONF_API_URL, CONF_CONNECTION_ID, CONF_PAIRING_CODE,
                                                DEFAULT_API_URL, DOMAIN, ENV_API_URL)
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import DEVICES_URL, OTHER_USER_ID, TOKEN, USER_ID, error_body, pairing_response

pytestmark = pytest.mark.usefixtures("mock_setup_entry")


async def start(hass: HomeAssistant) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return result


async def submit(hass: HomeAssistant, result: dict, code: str = "ABCD-EFGH") -> dict:
    return await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_PAIRING_CODE: code})


async def test_pairing_creates_an_entry_for_the_account(hass: HomeAssistant,
                                                         aioclient_mock: AiohttpClientMocker) -> None:
    hass.config.location_name = "Kitchen"
    aioclient_mock.post(DEVICES_URL, status=HTTPStatus.CREATED, json=pairing_response())

    result = await submit(hass, await start(hass), "  abcd-efgh ")

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "baker"
    assert result["result"].unique_id == USER_ID
    assert result["data"] == {CONF_API_URL: DEFAULT_API_URL, CONF_TOKEN: TOKEN,
                              CONF_CONNECTION_ID: "0196f2a0-0000-7000-8000-00000000c001"}
    # What was sent: the trimmed code, HA's name as the label, and the JSON content type breadsheet requires
    method, url, body, headers = aioclient_mock.mock_calls[0]
    assert body == {"code": "abcd-efgh", "label": "Kitchen"}
    assert headers["X-Breadsheet-Via"] == "ha"
    assert "Authorization" not in headers


async def test_long_names_are_cut_to_breadsheets_label_limit(hass: HomeAssistant,
                                                             aioclient_mock: AiohttpClientMocker) -> None:
    hass.config.location_name = "x" * 80
    aioclient_mock.post(DEVICES_URL, status=HTTPStatus.CREATED, json=pairing_response())
    await submit(hass, await start(hass))
    assert aioclient_mock.mock_calls[0][2]["label"] == "x" * 60


@pytest.mark.parametrize(
    ("response", "field", "error"),
    [
        ({"status": HTTPStatus.BAD_REQUEST, "json": error_body("pairing_code_invalid")}, CONF_PAIRING_CODE,
         "invalid_code"),
        ({"status": HTTPStatus.TOO_MANY_REQUESTS, "json": error_body("rate_limited"),
          "headers": {"Retry-After": "600"}}, "base", "rate_limited"),
        ({"status": HTTPStatus.SERVICE_UNAVAILABLE, "json": error_body("service_unavailable"),
          "headers": {"Retry-After": "30"}}, "base", "cannot_connect"),
        ({"status": HTTPStatus.BAD_GATEWAY, "text": "<html>Bad gateway</html>"}, "base", "cannot_connect"),
        ({"exc": aiohttp.ClientConnectionError()}, "base", "cannot_connect"),
        ({"exc": TimeoutError()}, "base", "cannot_connect"),
        ({"status": HTTPStatus.BAD_REQUEST, "json": error_body("body_invalid")}, "base", "unknown"),
        ({"status": HTTPStatus.CREATED, "json": {"token": TOKEN}}, "base", "unknown"),
    ],
)
async def test_errors_can_be_recovered_from(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                            response: dict, field: str, error: str) -> None:
    aioclient_mock.post(DEVICES_URL, **response)
    result = await submit(hass, await start(hass))
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: error}

    aioclient_mock.clear_requests()
    aioclient_mock.post(DEVICES_URL, status=HTTPStatus.CREATED, json=pairing_response())
    result = await submit(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_an_account_is_set_up_once(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                         mock_config_entry: MockConfigEntry) -> None:
    mock_config_entry.add_to_hass(hass)
    aioclient_mock.post(DEVICES_URL, status=HTTPStatus.CREATED, json=pairing_response(token="another-token"))

    result = await submit(hass, await start(hass))

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert mock_config_entry.data[CONF_TOKEN] == TOKEN


async def test_other_accounts_get_their_own_entry(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                                  mock_config_entry: MockConfigEntry) -> None:
    mock_config_entry.add_to_hass(hass)
    aioclient_mock.post(DEVICES_URL, status=HTTPStatus.CREATED,
                        json=pairing_response(user_id=OTHER_USER_ID, username="other"))
    result = await submit(hass, await start(hass))
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "other"


async def test_a_development_api_url_can_be_set(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_API_URL, "http://localhost:5050/")
    aioclient_mock.post("http://localhost:5050/api/v2/devices", status=HTTPStatus.CREATED, json=pairing_response())
    result = await submit(hass, await start(hass))
    assert result["data"][CONF_API_URL] == "http://localhost:5050/"


async def test_reauth_pairs_the_same_account_again(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                                   mock_config_entry: MockConfigEntry) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    assert result["description_placeholders"]["username"] == "baker"

    aioclient_mock.post(DEVICES_URL, status=HTTPStatus.BAD_REQUEST, json=error_body("pairing_code_invalid"))
    result = await submit(hass, result)
    assert result["errors"] == {CONF_PAIRING_CODE: "invalid_code"}

    aioclient_mock.clear_requests()
    aioclient_mock.post(DEVICES_URL, status=HTTPStatus.CREATED,
                        json=pairing_response(token="new-token", connection_id="new-connection"))
    result = await submit(hass, result)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_TOKEN] == "new-token"
    assert mock_config_entry.data[CONF_CONNECTION_ID] == "new-connection"


async def test_reauth_refuses_another_accounts_code(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                                    mock_config_entry: MockConfigEntry) -> None:
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    aioclient_mock.post(DEVICES_URL, status=HTTPStatus.CREATED,
                        json=pairing_response(user_id=OTHER_USER_ID, username="other", token="wrong"))

    result = await submit(hass, result)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert mock_config_entry.data[CONF_TOKEN] == TOKEN


async def test_reauth_keeps_the_entrys_api_url(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                               monkeypatch: pytest.MonkeyPatch,
                                               mock_config_entry: MockConfigEntry) -> None:
    monkeypatch.setenv(ENV_API_URL, "http://localhost:5050")  # ignored: the entry already has its URL
    mock_config_entry.add_to_hass(hass)
    result = await mock_config_entry.start_reauth_flow(hass)
    aioclient_mock.post(DEVICES_URL, status=HTTPStatus.CREATED, json=pairing_response(token="new-token"))
    result = await submit(hass, result)
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_API_URL] == DEFAULT_API_URL

"""Tests for the Breadsheet sidebar panel and its panel URL websocket command."""

from http import HTTPStatus

import aiohttp

from homeassistant.components import frontend
from homeassistant.core import HomeAssistant

from custom_components.breadsheet.panel import PANEL_URL_PATH, STATIC_URL
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import BAKE_ID, DEFAULT_API_URL, bake_json, error_body, mock_bakes

PANEL_TOKENS_URL = f"{DEFAULT_API_URL}/api/v2/panel-tokens"
PAGE = "https://www.breadsheet.com/panel#t=secret"


async def set_up(hass: HomeAssistant, entry: MockConfigEntry, aioclient_mock: AiohttpClientMocker) -> None:
    mock_bakes(aioclient_mock, [bake_json()], interval=60)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def ask(hass_ws_client, hass: HomeAssistant, **fields) -> dict:
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "breadsheet/panel_url", **fields})
    return await client.receive_json()


def panel_posts(aioclient_mock: AiohttpClientMocker) -> list:
    return [call[2] for call in aioclient_mock.mock_calls if str(call[1]) == PANEL_TOKENS_URL]


async def test_the_panel_is_in_the_sidebar_while_set_up(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                                        mock_config_entry: MockConfigEntry, hass_client) -> None:
    await set_up(hass, mock_config_entry, aioclient_mock)
    panel = hass.data[frontend.DATA_PANELS][PANEL_URL_PATH]
    assert (panel.sidebar_title, panel.sidebar_icon, panel.require_admin) == ("Breadsheet", "mdi:bread-slice", False)

    response = await (await hass_client()).get(STATIC_URL)
    assert response.status == HTTPStatus.OK
    assert 'customElements.define("breadsheet-panel"' in await response.text()

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert PANEL_URL_PATH not in hass.data[frontend.DATA_PANELS]


async def test_the_panel_asks_breadsheet_for_a_fresh_page(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                                          mock_config_entry: MockConfigEntry, hass_ws_client) -> None:
    await set_up(hass, mock_config_entry, aioclient_mock)
    aioclient_mock.post(PANEL_TOKENS_URL, status=HTTPStatus.CREATED,
                        json={"token": "secret", "expires_at": "2026-11-10T06:00:00+00:00", "url": PAGE})
    reply = await ask(hass_ws_client, hass)
    assert reply["success"] and reply["result"] == {"url": PAGE}
    assert panel_posts(aioclient_mock) == [{}]


async def test_a_bake_opens_directly(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                     mock_config_entry: MockConfigEntry, hass_ws_client) -> None:
    await set_up(hass, mock_config_entry, aioclient_mock)
    aioclient_mock.post(PANEL_TOKENS_URL, status=HTTPStatus.CREATED,
                        json={"token": "s", "expires_at": "x", "url": f"https://www.breadsheet.com/panel/{BAKE_ID}#t=s"})
    reply = await ask(hass_ws_client, hass, bake_id=BAKE_ID)
    assert reply["result"]["url"].startswith(f"https://www.breadsheet.com/panel/{BAKE_ID}")
    assert panel_posts(aioclient_mock) == [{"bake_id": BAKE_ID}]


async def test_an_ended_bake_shows_the_list(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                            mock_config_entry: MockConfigEntry, hass_ws_client) -> None:
    await set_up(hass, mock_config_entry, aioclient_mock)
    calls = []

    async def answer(method, url, data):
        calls.append(data)
        if data.get("bake_id"):
            return aiohttp_response(HTTPStatus.NOT_FOUND, error_body("not_found"))
        return aiohttp_response(HTTPStatus.CREATED, {"token": "s", "expires_at": "x", "url": PAGE})
    aioclient_mock.post(PANEL_TOKENS_URL, side_effect=answer)
    reply = await ask(hass_ws_client, hass, bake_id="gone")
    assert reply["result"] == {"url": PAGE}
    assert calls == [{"bake_id": "gone"}, {}]


async def test_breadsheet_down_is_a_clear_error(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                                mock_config_entry: MockConfigEntry, hass_ws_client) -> None:
    await set_up(hass, mock_config_entry, aioclient_mock)
    aioclient_mock.post(PANEL_TOKENS_URL, exc=aiohttp.ClientConnectionError())
    reply = await ask(hass_ws_client, hass)
    assert not reply["success"]
    assert reply["error"]["message"] == "Breadsheet can't be reached right now. Try again soon."


async def test_a_refusal_suggests_pairing_again(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                                mock_config_entry: MockConfigEntry, hass_ws_client) -> None:
    await set_up(hass, mock_config_entry, aioclient_mock)
    aioclient_mock.post(PANEL_TOKENS_URL, status=HTTPStatus.FORBIDDEN, json=error_body("scope_missing"))
    reply = await ask(hass_ws_client, hass)
    assert reply["error"]["message"] == "Breadsheet refused the panel. Try pairing again."


async def test_without_an_account_set_up(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                         mock_config_entry: MockConfigEntry, hass_ws_client) -> None:
    await set_up(hass, mock_config_entry, aioclient_mock)
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    reply = await ask(hass_ws_client, hass)
    assert reply["error"]["code"] == "not_found"


def aiohttp_response(status, body):
    from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse
    from yarl import URL
    return AiohttpClientMockResponse("POST", URL(PANEL_TOKENS_URL), status=status, json=body)

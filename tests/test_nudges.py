"""Tests for the webhook: registering its address with breadsheet, and polling when nudged."""

from datetime import timedelta
from http import HTTPStatus
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp
from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.core_config import async_process_ha_core_config

from custom_components.breadsheet.const import CONF_WEBHOOK_ID
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import BAKES_URL, WEBHOOK_URL, bake_json, error_body, mock_bakes

CLOUDHOOK = "https://hooks.nabu.casa/very-long-random-id"


class CloudNotAvailable(Exception):
    pass


def fake_cloud(*, subscribed: bool = True, connected: bool = True) -> SimpleNamespace:
    """Stands in for homeassistant.components.cloud, which can't be imported in this test environment."""
    cloud = SimpleNamespace(CloudNotAvailable=CloudNotAvailable, listeners={}, connected=connected)

    async def get_or_create(hass, webhook_id):
        if not cloud.connected:
            raise CloudNotAvailable
        return CLOUDHOOK
    cloud.async_active_subscription = lambda hass: subscribed
    cloud.async_get_or_create_cloudhook = get_or_create
    cloud.async_delete_cloudhook = AsyncMock()

    def listen(name):
        def add(hass, *args):
            cloud.listeners[name] = args[-1]
            return lambda: cloud.listeners.pop(name, None)
        return add
    cloud.async_listen_connection_change = listen("connection")
    cloud.async_listen_cloudhook_change = listen("cloudhook")
    return cloud


async def set_up(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def external_url(hass: HomeAssistant, url: str) -> None:
    await async_process_ha_core_config(hass, {"external_url": url})


def webhook_calls(aioclient_mock: AiohttpClientMocker) -> list[tuple[str, Any]]:
    """(method, body) of every call to breadsheet's webhook endpoint."""
    return [(call[0], call[2]) for call in aioclient_mock.mock_calls if str(call[1]) == WEBHOOK_URL]


def polls(aioclient_mock: AiohttpClientMocker) -> int:
    return sum(1 for call in aioclient_mock.mock_calls if str(call[1]) == BAKES_URL)


class TestRegistering:
    async def test_without_an_external_address_breadsheet_is_told_to_stop(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        mock_bakes(aioclient_mock, [], interval=120)
        await set_up(hass, mock_config_entry)
        assert webhook_calls(aioclient_mock) == [("DELETE", {})]
        assert mock_config_entry.runtime_data.nudges.kind == "none"

    async def test_an_external_https_address_is_registered(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        await external_url(hass, "https://ha.example.com")
        mock_bakes(aioclient_mock, [], interval=120)
        await set_up(hass, mock_config_entry)
        webhook_id = mock_config_entry.data[CONF_WEBHOOK_ID]
        assert webhook_calls(aioclient_mock) == [
            ("PUT", {"url": f"https://ha.example.com/api/webhook/{webhook_id}"})]
        assert mock_config_entry.runtime_data.nudges.kind == "external"

    @pytest.mark.parametrize("url", ["http://ha.example.com", "https://ha.example.com:8123"])
    async def test_breadsheet_only_calls_https_on_443(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            url: str) -> None:
        await external_url(hass, url)
        mock_bakes(aioclient_mock, [], interval=120)
        await set_up(hass, mock_config_entry)
        assert webhook_calls(aioclient_mock) == [("DELETE", {})]

    async def test_a_cloudhook_comes_first(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        await external_url(hass, "https://ha.example.com")
        mock_bakes(aioclient_mock, [], interval=120)
        with patch("custom_components.breadsheet.nudges.cloud_component", return_value=fake_cloud()):
            await set_up(hass, mock_config_entry)
        assert webhook_calls(aioclient_mock) == [("PUT", {"url": CLOUDHOOK})]
        assert mock_config_entry.runtime_data.nudges.kind == "cloud"

    async def test_cloud_connecting_later_registers_then(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        cloud = fake_cloud(connected=False)
        mock_bakes(aioclient_mock, [], interval=120)
        with patch("custom_components.breadsheet.nudges.cloud_component", return_value=cloud):
            await set_up(hass, mock_config_entry)
            assert webhook_calls(aioclient_mock) == []  # leaves breadsheet alone while Cloud connects
            cloud.connected = True
            cloud.listeners["connection"]("connected")
            await hass.async_block_till_done()
        assert webhook_calls(aioclient_mock) == [("PUT", {"url": CLOUDHOOK})]

    async def test_nothing_is_sent_again_when_nothing_changed(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        await external_url(hass, "https://ha.example.com")
        mock_bakes(aioclient_mock, [], interval=120)
        await set_up(hass, mock_config_entry)
        await mock_config_entry.runtime_data.nudges.async_register()
        assert len(webhook_calls(aioclient_mock)) == 1

    async def test_an_address_breadsheet_refuses_is_logged_once(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            caplog: pytest.LogCaptureFixture) -> None:
        await external_url(hass, "https://ha.internal.example")
        aioclient_mock.get(BAKES_URL, json={"bakes": []}, headers={"X-Breadsheet-Poll-Interval": "120"})
        aioclient_mock.put(WEBHOOK_URL, status=HTTPStatus.BAD_REQUEST,
                           json=error_body("webhook_url_invalid", "Webhooks must be on the public internet."))
        await set_up(hass, mock_config_entry)
        await mock_config_entry.runtime_data.nudges.async_register()
        assert len(webhook_calls(aioclient_mock)) == 1
        assert caplog.text.count("Breadsheet can't reach this Home Assistant") == 1
        assert "ha.internal.example" not in caplog.text  # the address never reaches the log
        assert mock_config_entry.runtime_data.nudges.kind == "none"

    async def test_registering_is_retried_after_the_next_good_poll(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            freezer: FrozenDateTimeFactory) -> None:
        await external_url(hass, "https://ha.example.com")
        aioclient_mock.get(BAKES_URL, json={"bakes": []}, headers={"X-Breadsheet-Poll-Interval": "120"})
        aioclient_mock.put(WEBHOOK_URL, exc=aiohttp.ClientConnectionError())
        await set_up(hass, mock_config_entry)

        aioclient_mock.clear_requests()
        mock_bakes(aioclient_mock, [], interval=120)
        freezer.tick(timedelta(seconds=121))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert [method for method, _ in webhook_calls(aioclient_mock)] == ["PUT"]

    async def test_re_pairing_keeps_the_webhook_id(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        mock_bakes(aioclient_mock, [], interval=120)
        await set_up(hass, mock_config_entry)
        webhook_id = mock_config_entry.data[CONF_WEBHOOK_ID]
        from .conftest import DEVICES_URL, pairing_response
        aioclient_mock.post(DEVICES_URL, status=HTTPStatus.CREATED, json=pairing_response(token="new-token"))
        result = await mock_config_entry.start_reauth_flow(hass)
        await hass.config_entries.flow.async_configure(result["flow_id"], {"pairing_code": "ABCD-EFGH"})
        await hass.async_block_till_done()
        assert mock_config_entry.data[CONF_WEBHOOK_ID] == webhook_id


class TestNudges:
    async def test_a_nudge_polls_at_once(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            hass_client_no_auth) -> None:
        mock_bakes(aioclient_mock, [], interval=120)
        await set_up(hass, mock_config_entry)
        before = polls(aioclient_mock)

        aioclient_mock.clear_requests()
        mock_bakes(aioclient_mock, [bake_json()], interval=60, etag='"v2"')
        client = await hass_client_no_auth()
        response = await client.post(f"/api/webhook/{mock_config_entry.data[CONF_WEBHOOK_ID]}", json={"anything": 1})
        await hass.async_block_till_done()

        assert response.status == HTTPStatus.OK
        assert before == 1 and polls(aioclient_mock) == 1
        assert list(mock_config_entry.runtime_data.coordinator.data.bakes)  # the new bake is in

    async def test_a_burst_of_nudges_costs_one_extra_poll(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            hass_client_no_auth, freezer: FrozenDateTimeFactory) -> None:
        mock_bakes(aioclient_mock, [], interval=120)
        await set_up(hass, mock_config_entry)
        aioclient_mock.clear_requests()
        mock_bakes(aioclient_mock, [], interval=120)
        client = await hass_client_no_auth()
        for _ in range(5):
            await client.post(f"/api/webhook/{mock_config_entry.data[CONF_WEBHOOK_ID]}")
        await hass.async_block_till_done()
        freezer.tick(timedelta(seconds=11))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert polls(aioclient_mock) == 2  # the first at once, the rest folded into one

    async def test_after_unloading_nudges_do_nothing(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            hass_client_no_auth) -> None:
        mock_bakes(aioclient_mock, [], interval=120)
        await set_up(hass, mock_config_entry)
        await hass.config_entries.async_unload(mock_config_entry.entry_id)
        aioclient_mock.clear_requests()
        client = await hass_client_no_auth()
        await client.post(f"/api/webhook/{mock_config_entry.data[CONF_WEBHOOK_ID]}")
        await hass.async_block_till_done()
        assert polls(aioclient_mock) == 0


class TestRemoval:
    async def test_removing_stops_nudges_and_deletes_the_cloudhook(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        cloud = fake_cloud()
        mock_bakes(aioclient_mock, [], interval=120)
        with patch("custom_components.breadsheet.nudges.cloud_component", return_value=cloud):
            await set_up(hass, mock_config_entry)
            aioclient_mock.clear_requests()
            from .conftest import mock_webhook
            mock_webhook(aioclient_mock)
            await hass.config_entries.async_remove(mock_config_entry.entry_id)
            await hass.async_block_till_done()
        assert webhook_calls(aioclient_mock) == [("DELETE", {})]
        cloud.async_delete_cloudhook.assert_awaited_once()

    async def test_removing_works_when_breadsheet_is_down(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        mock_bakes(aioclient_mock, [], interval=120)
        await set_up(hass, mock_config_entry)
        aioclient_mock.clear_requests()
        aioclient_mock.delete(WEBHOOK_URL, exc=aiohttp.ClientConnectionError())
        await hass.config_entries.async_remove(mock_config_entry.entry_id)
        await hass.async_block_till_done()


async def test_diagnostics_say_which_kind_but_not_the_address(
        hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
        hass_client) -> None:
    await external_url(hass, "https://ha.example.com")
    mock_bakes(aioclient_mock, [], interval=120)
    await set_up(hass, mock_config_entry)
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, mock_config_entry)
    assert diagnostics["webhook"] == "external"
    assert mock_config_entry.data[CONF_WEBHOOK_ID] not in str(diagnostics)

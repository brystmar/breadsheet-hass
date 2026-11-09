"""Tests for the polling coordinator: intervals, ETags, outages, restarts, and breadsheet_bake_ended."""

from datetime import timedelta
from http import HTTPStatus
from typing import Any

import aiohttp
from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.core import Event, HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.breadsheet.const import EVENT_BAKE_ENDED
from custom_components.breadsheet.coordinator import BreadsheetCoordinator, storage_key
from pytest_homeassistant_custom_component.common import (MockConfigEntry, async_capture_events,
                                                          async_fire_time_changed)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import BAKES_URL, BAKE_ID, OTHER_BAKE_ID, TOKEN, bake_json, error_body, mock_bakes

NOW = "2026-11-09T18:30:00+00:00"


@pytest.fixture(autouse=True)
def frozen(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    freezer.move_to(NOW)
    return freezer


async def set_up(hass: HomeAssistant, entry: MockConfigEntry) -> BreadsheetCoordinator:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry.runtime_data.coordinator if entry.state is ConfigEntryState.LOADED else None


async def wait(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    """Let time pass, running any polls that come due."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def answer(aioclient_mock: AiohttpClientMocker, *args: Any, **kwargs: Any) -> None:
    """Replace breadsheet's answer to the next polls."""
    aioclient_mock.clear_requests()
    mock_bakes(aioclient_mock, *args, **kwargs)


def saved(hass_storage: dict[str, Any], entry: MockConfigEntry) -> dict[str, Any]:
    return hass_storage[storage_key(entry.entry_id)]["data"]


class TestPolling:
    async def test_setup_polls_for_active_fancy_bakes_as_the_device(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        mock_bakes(aioclient_mock, [bake_json()], interval=60)
        coordinator = await set_up(hass, mock_config_entry)

        assert list(coordinator.data.bakes) == [BAKE_ID]
        bake = coordinator.data.bakes[BAKE_ID]
        assert (bake.name, len(bake.steps), bake.steps[0].hands_on_seconds) == ("Weekend Loaf", 3, 600)
        assert coordinator.data.last_synced == dt_util.parse_datetime(NOW)
        _, _, _, headers = aioclient_mock.mock_calls[0]
        assert headers["Authorization"] == f"Bearer {TOKEN}"
        assert "If-None-Match" not in headers

    @pytest.mark.parametrize(("suggested", "bakes", "expected"), [
        (60, [bake_json()], 60), (900, [], 900), (5, [], 60), (99999, [], 1800),
        (None, [bake_json()], 60), (None, [], 120),
    ])
    async def test_the_interval_follows_breadsheet_within_limits(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            suggested: int | None, bakes: list, expected: int) -> None:
        mock_bakes(aioclient_mock, bakes, interval=suggested)
        coordinator = await set_up(hass, mock_config_entry)
        assert coordinator.update_interval == timedelta(seconds=expected)

    async def test_polls_come_at_the_suggested_interval(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            frozen: FrozenDateTimeFactory) -> None:
        mock_bakes(aioclient_mock, [], interval=900)
        await set_up(hass, mock_config_entry)
        await wait(hass, frozen, 600)
        assert aioclient_mock.call_count == 1
        await wait(hass, frozen, 301)
        assert aioclient_mock.call_count == 2

    async def test_an_unchanged_list_is_a_cheap_304(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            frozen: FrozenDateTimeFactory) -> None:
        mock_bakes(aioclient_mock, [bake_json()], interval=60, etag='"v1"')
        coordinator = await set_up(hass, mock_config_entry)

        answer(aioclient_mock, status=HTTPStatus.NOT_MODIFIED, interval=60, etag='"v1"')
        await wait(hass, frozen, 61)

        _, _, _, headers = aioclient_mock.mock_calls[0]
        assert headers["If-None-Match"] == '"v1"'
        assert list(coordinator.data.bakes) == [BAKE_ID]
        assert coordinator.data.last_synced == dt_util.utcnow()


class TestOutages:
    async def test_a_failed_poll_keeps_the_bakes_until_their_projected_end(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            frozen: FrozenDateTimeFactory) -> None:
        mock_bakes(aioclient_mock, [bake_json(projected_end="2026-11-09T20:00:00+00:00")], interval=60)
        coordinator = await set_up(hass, mock_config_entry)
        synced = coordinator.data.last_synced

        answer(aioclient_mock, exc=aiohttp.ClientConnectionError())
        await wait(hass, frozen, 61)

        assert not coordinator.last_update_success
        bake = coordinator.data.bakes[BAKE_ID]
        assert coordinator.data.last_synced == synced
        assert coordinator.bake_available(bake)
        frozen.move_to("2026-11-09T20:00:01+00:00")
        assert not coordinator.bake_available(bake)

    @pytest.mark.parametrize("response", [
        {"status": HTTPStatus.SERVICE_UNAVAILABLE, "json": error_body("service_unavailable")},
        {"status": HTTPStatus.TOO_MANY_REQUESTS, "json": error_body("rate_limited")},
    ])
    async def test_retry_after_is_honored(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            frozen: FrozenDateTimeFactory, response: dict) -> None:
        mock_bakes(aioclient_mock, [bake_json()], interval=60)
        await set_up(hass, mock_config_entry)

        aioclient_mock.clear_requests()
        aioclient_mock.get(BAKES_URL, headers={"Retry-After": "300"}, **response)
        await wait(hass, frozen, 61)  # the regular poll, which is told to wait 5 minutes
        await wait(hass, frozen, 120)
        assert aioclient_mock.call_count == 1
        await wait(hass, frozen, 181)
        assert aioclient_mock.call_count == 2

    async def test_a_malformed_list_is_a_failed_poll(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            frozen: FrozenDateTimeFactory) -> None:
        mock_bakes(aioclient_mock, [bake_json()], interval=60)
        coordinator = await set_up(hass, mock_config_entry)
        ended = async_capture_events(hass, EVENT_BAKE_ENDED)

        answer(aioclient_mock, [{"id": "half a bake"}], interval=60)
        await wait(hass, frozen, 61)

        assert not coordinator.last_update_success
        assert list(coordinator.data.bakes) == [BAKE_ID]
        assert ended == []

    async def test_a_revoked_token_asks_to_pair_again(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            frozen: FrozenDateTimeFactory) -> None:
        mock_bakes(aioclient_mock, [bake_json()], interval=60)
        await set_up(hass, mock_config_entry)

        answer(aioclient_mock, status=HTTPStatus.UNAUTHORIZED, json=error_body("token_invalid"))
        await wait(hass, frozen, 61)

        flows = hass.config_entries.flow.async_progress()
        assert [(flow["context"]["source"], flow["step_id"]) for flow in flows] == [(SOURCE_REAUTH, "reauth_confirm")]

    async def test_with_nothing_saved_setup_waits_for_breadsheet(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        mock_bakes(aioclient_mock, exc=aiohttp.ClientConnectionError())
        await set_up(hass, mock_config_entry)
        assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


class TestRestarts:
    async def test_the_last_good_bakes_are_saved(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            hass_storage: dict[str, Any]) -> None:
        mock_bakes(aioclient_mock, [bake_json()], interval=60, etag='"v1"')
        await set_up(hass, mock_config_entry)
        data = saved(hass_storage, mock_config_entry)
        assert (data["etag"], [bake["id"] for bake in data["bakes"]]) == ('"v1"', [BAKE_ID])
        assert TOKEN not in str(data)

    async def test_starting_during_an_outage_runs_from_the_saved_bakes(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            hass_storage: dict[str, Any], frozen: FrozenDateTimeFactory) -> None:
        hass_storage[storage_key(mock_config_entry.entry_id)] = {
            "version": 1, "key": storage_key(mock_config_entry.entry_id),
            "data": {"etag": '"v1"', "last_synced": "2026-11-09T18:20:00+00:00", "bakes": [bake_json()]}}
        mock_bakes(aioclient_mock, exc=aiohttp.ClientConnectionError())

        coordinator = await set_up(hass, mock_config_entry)

        assert mock_config_entry.state is ConfigEntryState.LOADED
        assert list(coordinator.data.bakes) == [BAKE_ID]
        assert coordinator.data.last_synced == dt_util.parse_datetime("2026-11-09T18:20:00+00:00")
        assert coordinator.update_interval == timedelta(seconds=60)  # try again soon
        bake = coordinator.data.bakes[BAKE_ID]
        assert coordinator.bake_available(bake)
        frozen.move_to("2026-11-10T02:00:01+00:00")  # past its projected end, and still not synced
        assert not coordinator.bake_available(bake)
        frozen.move_to(NOW)

        answer(aioclient_mock, status=HTTPStatus.NOT_MODIFIED, interval=60, etag='"v1"')
        await wait(hass, frozen, 61)
        assert coordinator.last_update_success
        assert aioclient_mock.mock_calls[0][3]["If-None-Match"] == '"v1"'
        assert list(coordinator.data.bakes) == [BAKE_ID]

    async def test_a_bake_that_ended_during_a_restart_still_fires(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            hass_storage: dict[str, Any]) -> None:
        hass_storage[storage_key(mock_config_entry.entry_id)] = {
            "version": 1, "key": storage_key(mock_config_entry.entry_id),
            "data": {"etag": '"v1"', "last_synced": "2026-11-09T18:20:00+00:00",
                     "bakes": [bake_json(), bake_json(OTHER_BAKE_ID)]}}
        ended = async_capture_events(hass, EVENT_BAKE_ENDED)
        mock_bakes(aioclient_mock, [bake_json(OTHER_BAKE_ID)], interval=60, etag='"v2"')

        await set_up(hass, mock_config_entry)

        assert [event.data for event in ended] == [{"bake_id": BAKE_ID}]

    async def test_unreadable_saved_bakes_are_ignored(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            hass_storage: dict[str, Any]) -> None:
        hass_storage[storage_key(mock_config_entry.entry_id)] = {
            "version": 1, "key": storage_key(mock_config_entry.entry_id), "data": {"bakes": [{"id": 1}]}}
        mock_bakes(aioclient_mock, [], interval=900)
        coordinator = await set_up(hass, mock_config_entry)
        assert coordinator.data.bakes == {}
        assert "If-None-Match" not in aioclient_mock.mock_calls[0][3]

    async def test_removing_the_integration_deletes_the_saved_bakes(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            hass_storage: dict[str, Any]) -> None:
        mock_bakes(aioclient_mock, [bake_json()], interval=60)
        await set_up(hass, mock_config_entry)
        assert storage_key(mock_config_entry.entry_id) in hass_storage
        await hass.config_entries.async_remove(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        assert storage_key(mock_config_entry.entry_id) not in hass_storage


class TestBakeEnded:
    async def test_a_bake_leaving_the_list_fires_once(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            frozen: FrozenDateTimeFactory) -> None:
        mock_bakes(aioclient_mock, [bake_json(), bake_json(OTHER_BAKE_ID)], interval=60, etag='"v1"')
        coordinator = await set_up(hass, mock_config_entry)
        ended: list[Event] = async_capture_events(hass, EVENT_BAKE_ENDED)

        answer(aioclient_mock, [bake_json(OTHER_BAKE_ID)], interval=60, etag='"v2"')
        await wait(hass, frozen, 61)
        await wait(hass, frozen, 61)  # still gone: no second event

        assert [event.data for event in ended] == [{"bake_id": BAKE_ID}]
        assert list(coordinator.data.bakes) == [OTHER_BAKE_ID]

    async def test_failed_polls_and_304s_never_fire(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            frozen: FrozenDateTimeFactory) -> None:
        mock_bakes(aioclient_mock, [bake_json()], interval=60, etag='"v1"')
        await set_up(hass, mock_config_entry)
        ended = async_capture_events(hass, EVENT_BAKE_ENDED)

        answer(aioclient_mock, status=HTTPStatus.SERVICE_UNAVAILABLE, json=error_body("service_unavailable"))
        await wait(hass, frozen, 61)
        answer(aioclient_mock, status=HTTPStatus.NOT_MODIFIED, interval=60, etag='"v1"')
        await wait(hass, frozen, 61)

        assert ended == []

    async def test_nothing_fires_on_the_very_first_poll(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        ended = async_capture_events(hass, EVENT_BAKE_ENDED)
        mock_bakes(aioclient_mock, [], interval=900)
        await set_up(hass, mock_config_entry)
        assert ended == []

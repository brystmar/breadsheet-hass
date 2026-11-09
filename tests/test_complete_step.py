"""Tests for breadsheet.complete_step: local re-projection, delivery, the retry queue, and restarts."""

from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from typing import Any

import aiohttp
from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from custom_components.breadsheet.checkoffs import checkoffs_storage_key
from custom_components.breadsheet.const import DOMAIN, EVENT_STEP_LATE
from custom_components.breadsheet.models import Bake
from custom_components.breadsheet.steps import with_check_off
from pytest_homeassistant_custom_component.common import (MockConfigEntry, async_capture_events,
                                                          async_fire_time_changed)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import BAKE_ID, bake_json, error_body, mock_bakes, step_json

NOW = "2026-11-09T19:07:00+00:00"  # step 2 of under_way() was due at 19:05
CHECK_OFF_URL = f"https://api.breadsheet.com/api/v2/bakes/{BAKE_ID}/steps/2/complete"


@pytest.fixture(autouse=True)
def frozen(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    freezer.move_to(NOW)
    return freezer


def under_way() -> dict:
    """Step 1 checked off at 18:05 (wait 1 hour); step 2 due 19:05 (wait 7 hours); step 3 due 02:05."""
    bake = bake_json(projected_end="2026-11-10T02:05:00+00:00")
    bake["status"] = "in_progress"
    bake["steps"] = [step_json(1, "2026-11-09T18:00:00+00:00", 3600, completed_at="2026-11-09T18:05:00+00:00"),
                     step_json(2, "2026-11-09T19:05:00+00:00", 25200),
                     step_json(3, "2026-11-10T02:05:00+00:00", 0)]
    return bake


def checked_off(at: str = NOW) -> dict:
    """What breadsheet has after step 2 is checked off at `at`."""
    bake = under_way()
    bake["steps"][1] = step_json(2, "2026-11-09T19:05:00+00:00", 25200, completed_at=at)
    later = (dt_util.parse_datetime(at) + timedelta(hours=7)).isoformat()
    bake["steps"][2] = step_json(3, later, 0)
    bake["projected_end"] = later
    return bake


async def set_up(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def complete(hass: HomeAssistant, position: int = 2, bake_id: str = BAKE_ID) -> None:
    await hass.services.async_call(DOMAIN, "complete_step", {"bake_id": bake_id, "position": position},
                                   blocking=True)
    await hass.async_block_till_done()


async def wait(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def next_step(hass: HomeAssistant) -> str:
    return hass.states.get("sensor.weekend_loaf_next_step").state


def posts(aioclient_mock: AiohttpClientMocker) -> list[Any]:
    return [call for call in aioclient_mock.mock_calls if call[0] == "POST"]


def queued(hass_storage: dict[str, Any], entry: MockConfigEntry) -> list[dict]:
    return hass_storage.get(checkoffs_storage_key(entry.entry_id), {}).get("data", {}).get("checkoffs", [])


class TestReprojection:
    def test_later_steps_follow_the_check_off(self) -> None:
        bake = Bake.from_api(under_way())
        early = datetime(2026, 11, 9, 18, 50, tzinfo=timezone.utc)
        moved = with_check_off(bake, 2, early)
        assert moved.steps[1].completed_at == early
        assert moved.steps[2].scheduled_at == early + timedelta(hours=7)
        assert moved.projected_end == early + timedelta(hours=7)
        assert moved.steps[0] == bake.steps[0]  # earlier steps keep their times
        assert Bake.from_api(checked_off("2026-11-09T18:50:00+00:00")) == moved  # what breadsheet would say

    def test_skipped_steps_are_passed_over(self) -> None:
        raw = under_way()
        raw["steps"].insert(2, step_json(3, None, 600, skipped=True))
        raw["steps"][3]["position"] = 4
        moved = with_check_off(Bake.from_api(raw), 2, datetime(2026, 11, 9, 19, 0, tzinfo=timezone.utc))
        assert moved.steps[2].scheduled_at is None
        assert moved.steps[3].scheduled_at == datetime(2026, 11, 10, 2, 0, tzinfo=timezone.utc)

    def test_a_step_already_done_is_unchanged(self) -> None:
        bake = Bake.from_api(under_way())
        assert with_check_off(bake, 1, datetime(2026, 11, 9, 19, 0, tzinfo=timezone.utc)) is bake
        assert with_check_off(bake, 9, datetime(2026, 11, 9, 19, 0, tzinfo=timezone.utc)) is bake


class TestCompleteStep:
    async def test_it_shows_at_once_and_reaches_breadsheet(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        mock_bakes(aioclient_mock, [under_way()], interval=60)
        await set_up(hass, mock_config_entry)
        assert next_step(hass) == "Step 2"

        aioclient_mock.clear_requests()  # breadsheet takes the check-off, and the refresh after it shows it
        aioclient_mock.post(CHECK_OFF_URL, json={"bake": checked_off()})
        mock_bakes(aioclient_mock, [checked_off()], interval=60, etag='"v2"')
        await complete(hass)

        assert next_step(hass) == "Step 3"
        method, _, body, headers = posts(aioclient_mock)[0]
        assert body == {"completed_at": "2026-11-09T19:07:00+00:00"}
        assert headers["Authorization"].startswith("Bearer ")
        assert aioclient_mock.mock_calls[-1][0] == "GET"  # then a refresh

    async def test_the_late_reminder_doesnt_fire(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            frozen: FrozenDateTimeFactory) -> None:
        late = async_capture_events(hass, EVENT_STEP_LATE)
        mock_bakes(aioclient_mock, [under_way()], interval=60)
        aioclient_mock.post(CHECK_OFF_URL, exc=aiohttp.ClientConnectionError())
        await set_up(hass, mock_config_entry)
        await complete(hass)  # before step 2's late reminder at 19:10, during an outage
        await wait(hass, frozen, 240)
        assert late == []

    async def test_a_step_already_done_sends_nothing(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        mock_bakes(aioclient_mock, [under_way()], interval=60)
        await set_up(hass, mock_config_entry)
        await complete(hass, position=1)
        assert posts(aioclient_mock) == []

    @pytest.mark.parametrize(("bake_id", "position", "key"), [("nope", 2, "unknown_bake"), (BAKE_ID, 9, "unknown_step")])
    async def test_unknown_bakes_and_steps_are_errors(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            bake_id: str, position: int, key: str) -> None:
        mock_bakes(aioclient_mock, [under_way()], interval=60)
        await set_up(hass, mock_config_entry)
        with pytest.raises(ServiceValidationError) as caught:
            await complete(hass, position=position, bake_id=bake_id)
        assert caught.value.translation_key == key


class TestOutages:
    async def test_a_check_off_waits_and_retries_with_its_original_time(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            frozen: FrozenDateTimeFactory, hass_storage: dict[str, Any]) -> None:
        mock_bakes(aioclient_mock, [under_way()], interval=60)
        aioclient_mock.post(CHECK_OFF_URL, status=HTTPStatus.SERVICE_UNAVAILABLE, json=error_body("service_unavailable"))
        await set_up(hass, mock_config_entry)

        await complete(hass)
        assert next_step(hass) == "Step 3"
        assert [item["completed_at"] for item in queued(hass_storage, mock_config_entry)] == [
            "2026-11-09T19:07:00+00:00"]

        aioclient_mock.clear_requests()
        mock_bakes(aioclient_mock, [checked_off()], interval=60, etag='"v2"')
        aioclient_mock.post(CHECK_OFF_URL, json={"bake": checked_off()})
        await wait(hass, frozen, 10)
        assert posts(aioclient_mock) == []  # first retry after 15 seconds
        await wait(hass, frozen, 6)
        assert posts(aioclient_mock)[0][2] == {"completed_at": "2026-11-09T19:07:00+00:00"}
        assert queued(hass_storage, mock_config_entry) == []

    async def test_retry_after_is_honored(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            frozen: FrozenDateTimeFactory) -> None:
        mock_bakes(aioclient_mock, [under_way()], interval=60)
        aioclient_mock.post(CHECK_OFF_URL, status=HTTPStatus.TOO_MANY_REQUESTS, json=error_body("rate_limited"),
                            headers={"Retry-After": "90"})
        await set_up(hass, mock_config_entry)
        await complete(hass)
        await wait(hass, frozen, 60)
        assert len(posts(aioclient_mock)) == 1
        await wait(hass, frozen, 31)
        assert len(posts(aioclient_mock)) == 2

    async def test_polls_dont_undo_a_waiting_check_off(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            frozen: FrozenDateTimeFactory) -> None:
        mock_bakes(aioclient_mock, [under_way()], interval=60)
        aioclient_mock.post(CHECK_OFF_URL, exc=aiohttp.ClientConnectionError())
        await set_up(hass, mock_config_entry)
        await complete(hass)

        aioclient_mock.clear_requests()  # the poll works, but the check-off still can't get through
        mock_bakes(aioclient_mock, [under_way()], interval=60, etag='"v2"')
        aioclient_mock.post(CHECK_OFF_URL, exc=aiohttp.ClientConnectionError())
        await wait(hass, frozen, 61)
        assert next_step(hass) == "Step 3"

    async def test_a_refused_check_off_is_dropped(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            hass_storage: dict[str, Any]) -> None:
        mock_bakes(aioclient_mock, [under_way()], interval=60)
        aioclient_mock.post(CHECK_OFF_URL, status=HTTPStatus.CONFLICT, json=error_body("bake_completed"))
        await set_up(hass, mock_config_entry)
        await complete(hass)
        assert queued(hass_storage, mock_config_entry) == []
        assert next_step(hass) == "Step 2"  # refreshed from breadsheet, which never took it

    async def test_a_revoked_token_keeps_the_check_off_and_asks_to_pair(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            hass_storage: dict[str, Any]) -> None:
        mock_bakes(aioclient_mock, [under_way()], interval=60)
        aioclient_mock.post(CHECK_OFF_URL, status=HTTPStatus.UNAUTHORIZED, json=error_body("token_invalid"))
        await set_up(hass, mock_config_entry)
        await complete(hass)
        assert len(queued(hass_storage, mock_config_entry)) == 1
        assert [flow["context"]["source"] for flow in hass.config_entries.flow.async_progress()] == [SOURCE_REAUTH]

    async def test_tapping_twice_queues_once(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            frozen: FrozenDateTimeFactory, hass_storage: dict[str, Any]) -> None:
        mock_bakes(aioclient_mock, [under_way()], interval=60)
        aioclient_mock.post(CHECK_OFF_URL, exc=aiohttp.ClientConnectionError())
        await set_up(hass, mock_config_entry)
        await complete(hass)
        coordinator = mock_config_entry.runtime_data.coordinator
        await mock_config_entry.runtime_data.checkoffs.async_check_off(BAKE_ID, 2, dt_util.utcnow())
        assert len(queued(hass_storage, mock_config_entry)) == 1
        assert coordinator.data.bakes[BAKE_ID].steps[1].completed_at == dt_util.parse_datetime(NOW)


class TestRestarts:
    async def test_waiting_check_offs_are_sent_after_a_restart(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            hass_storage: dict[str, Any]) -> None:
        hass_storage[checkoffs_storage_key(mock_config_entry.entry_id)] = {
            "version": 1, "key": checkoffs_storage_key(mock_config_entry.entry_id), "data": {"checkoffs": [
                {"bake_id": BAKE_ID, "position": 2, "completed_at": "2026-11-09T19:06:00+00:00", "attempts": 3},
                {"bake_id": BAKE_ID, "position": "not a number", "completed_at": "x"}]}}
        mock_bakes(aioclient_mock, [under_way()], interval=60)
        aioclient_mock.post(CHECK_OFF_URL, json={"bake": checked_off("2026-11-09T19:06:00+00:00")})
        await set_up(hass, mock_config_entry)
        assert posts(aioclient_mock)[0][2] == {"completed_at": "2026-11-09T19:06:00+00:00"}
        assert queued(hass_storage, mock_config_entry) == []

    async def test_very_old_check_offs_are_given_up(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            hass_storage: dict[str, Any]) -> None:
        hass_storage[checkoffs_storage_key(mock_config_entry.entry_id)] = {
            "version": 1, "key": checkoffs_storage_key(mock_config_entry.entry_id), "data": {"checkoffs": [
                {"bake_id": BAKE_ID, "position": 2, "completed_at": "2026-11-06T19:06:00+00:00"}]}}
        mock_bakes(aioclient_mock, [under_way()], interval=60)
        await set_up(hass, mock_config_entry)
        assert posts(aioclient_mock) == []
        assert queued(hass_storage, mock_config_entry) == []

    async def test_removing_the_integration_deletes_waiting_check_offs(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            hass_storage: dict[str, Any]) -> None:
        mock_bakes(aioclient_mock, [under_way()], interval=60)
        aioclient_mock.post(CHECK_OFF_URL, exc=aiohttp.ClientConnectionError())
        await set_up(hass, mock_config_entry)
        await complete(hass)
        await hass.config_entries.async_remove(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        assert checkoffs_storage_key(mock_config_entry.entry_id) not in hass_storage

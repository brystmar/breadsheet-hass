"""Tests for the bake devices, sensors, and the calendar."""

from datetime import timedelta
from http import HTTPStatus

import aiohttp
from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.breadsheet.const import DOMAIN
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import BAKE_ID, OTHER_BAKE_ID, bake_json, mock_bakes, step_json

NOW = "2026-11-09T18:30:00+00:00"


@pytest.fixture(autouse=True)
def frozen(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    freezer.move_to(NOW)
    return freezer


async def set_up(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def wait(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def under_way() -> dict:
    """Weekend Loaf with step 1 checked off at 18:05; step 2 due at 19:05."""
    bake = bake_json()
    bake["status"] = "in_progress"
    bake["steps"][0] = step_json(1, "2026-11-09T18:00:00+00:00", 3600, completed_at="2026-11-09T18:05:00+00:00")
    bake["steps"][1] = step_json(2, "2026-11-09T19:05:00+00:00", 25200)
    bake["steps"][2] = step_json(3, "2026-11-10T02:05:00+00:00", 0)
    bake["projected_end"] = "2026-11-10T02:05:00+00:00"
    return bake


def bake_device(hass: HomeAssistant, bake_id: str) -> dr.DeviceEntry | None:
    devices = dr.async_get(hass).async_get_devices(identifiers={(DOMAIN, bake_id)})
    return devices[0] if devices else None


def bake_entity(hass: HomeAssistant, bake_id: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{bake_id}_{key}")
    assert entity_id, f"no {key} sensor for {bake_id}"
    return entity_id


class TestSensors:
    async def test_each_bake_gets_a_device_and_four_sensors(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        mock_bakes(aioclient_mock, [under_way()], interval=60)
        await set_up(hass, mock_config_entry)

        device = bake_device(hass, BAKE_ID)
        assert (device.name, device.model) == ("Weekend Loaf", "Bake")
        assert device.configuration_url == "https://www.breadsheet.com/bakes/abc123"

        current = hass.states.get(bake_entity(hass, BAKE_ID, "current_step"))
        assert current.state == "Step 1"
        assert current.attributes["bake_id"] == BAKE_ID
        assert current.attributes["completed_at"] == "2026-11-09T18:05:00+00:00"
        assert current.attributes["last_synced"] == "2026-11-09T18:30:00+00:00"

        upcoming = hass.states.get(bake_entity(hass, BAKE_ID, "next_step"))
        assert (upcoming.state, upcoming.attributes["position"]) == ("Step 2", 2)
        assert upcoming.attributes["hands_on_estimated"] is True

        time = hass.states.get(bake_entity(hass, BAKE_ID, "next_step_time"))
        assert dt_util.parse_datetime(time.state) == dt_util.parse_datetime("2026-11-09T19:05:00+00:00")

        progress = hass.states.get(bake_entity(hass, BAKE_ID, "progress"))
        assert (progress.state, progress.attributes["steps_done"], progress.attributes["steps_total"]) == ("33", 1, 3)

    async def test_before_the_first_check_off(self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                              mock_config_entry: MockConfigEntry) -> None:
        mock_bakes(aioclient_mock, [bake_json()], interval=60)
        await set_up(hass, mock_config_entry)
        assert hass.states.get(bake_entity(hass, BAKE_ID, "current_step")).state == "Not started"
        assert hass.states.get(bake_entity(hass, BAKE_ID, "next_step")).state == "Step 1"
        assert hass.states.get(bake_entity(hass, BAKE_ID, "progress")).state == "0"

    async def test_skipped_steps_dont_count(self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                            mock_config_entry: MockConfigEntry) -> None:
        bake = bake_json()
        bake["steps"][0] = step_json(1, None, 3600, skipped=True)
        mock_bakes(aioclient_mock, [bake], interval=60)
        await set_up(hass, mock_config_entry)
        assert hass.states.get(bake_entity(hass, BAKE_ID, "next_step")).state == "Step 2"
        assert hass.states.get(bake_entity(hass, BAKE_ID, "progress")).attributes["steps_total"] == 2

    async def test_long_step_text_is_cut_to_fit_a_state(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        bake = bake_json()
        bake["steps"][0]["text"] = "Fold " * 100
        mock_bakes(aioclient_mock, [bake], interval=60)
        await set_up(hass, mock_config_entry)
        state = hass.states.get(bake_entity(hass, BAKE_ID, "next_step"))
        assert len(state.state) == 255 and state.state.endswith("…")
        assert state.attributes["text"] == "Fold " * 100

    async def test_new_bakes_get_entities_and_ended_ones_lose_their_device(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            frozen: FrozenDateTimeFactory) -> None:
        mock_bakes(aioclient_mock, [bake_json()], interval=60)
        await set_up(hass, mock_config_entry)

        aioclient_mock.clear_requests()
        mock_bakes(aioclient_mock, [bake_json(OTHER_BAKE_ID, name="Pizza Dough")], interval=60, etag='"v2"')
        await wait(hass, frozen, 61)

        assert bake_device(hass, BAKE_ID) is None
        assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{BAKE_ID}_next_step") is None
        assert bake_device(hass, OTHER_BAKE_ID).name == "Pizza Dough"
        assert hass.states.get(bake_entity(hass, OTHER_BAKE_ID, "next_step")).state == "Step 1"

    async def test_a_bake_that_ended_while_ha_was_off_loses_its_device(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        mock_config_entry.add_to_hass(hass)
        stale = dr.async_get(hass).async_get_or_create(
            config_entry_id=mock_config_entry.entry_id, identifiers={(DOMAIN, OTHER_BAKE_ID)},
            name="Old bake", model="Bake")
        mock_bakes(aioclient_mock, [bake_json()], interval=60)
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
        assert dr.async_get(hass).async_get(stale.id) is None

    async def test_sensors_stay_up_through_an_outage_until_the_projected_end(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            frozen: FrozenDateTimeFactory) -> None:
        mock_bakes(aioclient_mock, [bake_json(projected_end="2026-11-09T18:33:00+00:00")], interval=60)
        await set_up(hass, mock_config_entry)
        entity_id = bake_entity(hass, BAKE_ID, "next_step")

        aioclient_mock.clear_requests()
        mock_bakes(aioclient_mock, exc=aiohttp.ClientConnectionError())
        await wait(hass, frozen, 61)
        assert hass.states.get(entity_id).state == "Step 1"

        await wait(hass, frozen, 60)
        await wait(hass, frozen, 61)  # past the projected end, still failing
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE

    async def test_no_link_to_production_from_a_dev_backend(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
        from homeassistant.const import CONF_TOKEN
        from custom_components.breadsheet.const import CONF_API_URL
        entry = MockConfigEntry(domain=DOMAIN, title="baker", unique_id="dev-user",
                                data={CONF_API_URL: "http://localhost:5050", CONF_TOKEN: "t"})
        aioclient_mock.delete("http://localhost:5050/api/v2/device/webhook", status=204)
        aioclient_mock.get("http://localhost:5050/api/v2/bakes?status=active&alert=fancy",
                           json={"bakes": [bake_json()]}, headers={"X-Breadsheet-Poll-Interval": "60"})
        await set_up(hass, entry)
        assert bake_device(hass, BAKE_ID).configuration_url is None


class TestCalendar:
    async def test_one_event_per_step(self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                      mock_config_entry: MockConfigEntry) -> None:
        bake = under_way()
        bake["steps"][1]["note"] = "Wet hands"
        mock_bakes(aioclient_mock, [bake], interval=60)
        await set_up(hass, mock_config_entry)

        response = await hass.services.async_call(
            "calendar", "get_events", {"entity_id": "calendar.breadsheet_baker_bakes",
                                       "start_date_time": "2026-11-09T00:00:00+00:00",
                                       "end_date_time": "2026-11-11T00:00:00+00:00"},
            blocking=True, return_response=True)
        events = response["calendar.breadsheet_baker_bakes"]["events"]
        assert [event["summary"] for event in events] == [
            "Weekend Loaf · Step 1", "Weekend Loaf · Step 2", "Weekend Loaf · Step 3"]
        assert events[1]["description"] == "Wet hands"
        start, end = dt_util.parse_datetime(events[1]["start"]), dt_util.parse_datetime(events[1]["end"])
        assert end - start == timedelta(minutes=10)  # hands-on time

    async def test_the_state_is_on_during_a_steps_hands_on_time(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            frozen: FrozenDateTimeFactory) -> None:
        mock_bakes(aioclient_mock, [under_way()], interval=60)
        await set_up(hass, mock_config_entry)
        calendar = hass.states.get("calendar.breadsheet_baker_bakes")
        assert calendar.state == "off"
        assert calendar.attributes["message"] == "Weekend Loaf · Step 2"

        frozen.move_to("2026-11-09T19:06:00+00:00")
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert hass.states.get("calendar.breadsheet_baker_bakes").state == "on"

    async def test_skipped_steps_have_no_event(self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
                                               mock_config_entry: MockConfigEntry) -> None:
        bake = bake_json()
        bake["steps"][1] = step_json(2, None, 25200, skipped=True)
        mock_bakes(aioclient_mock, [bake], interval=60)
        await set_up(hass, mock_config_entry)
        events = await hass.data["calendar"].get_entity("calendar.breadsheet_baker_bakes").async_get_events(
            hass, dt_util.parse_datetime("2026-11-09T00:00:00+00:00"),
            dt_util.parse_datetime("2026-11-11T00:00:00+00:00"))
        assert [event.summary for event in events] == ["Weekend Loaf · Step 1", "Weekend Loaf · Step 3"]

    async def test_with_no_bakes_the_calendar_is_empty_but_available(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        mock_bakes(aioclient_mock, [], interval=120)
        await set_up(hass, mock_config_entry)
        assert hass.states.get("calendar.breadsheet_baker_bakes").state == "off"

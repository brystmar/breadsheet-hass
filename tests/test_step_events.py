"""Tests for breadsheet_step_upcoming, breadsheet_step_due, and breadsheet_step_late."""

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.core import Event, HomeAssistant

from custom_components.breadsheet.const import (CONF_LEAD_MINUTES, EVENT_STEP_DUE, EVENT_STEP_LATE,
                                                EVENT_STEP_UPCOMING)
from pytest_homeassistant_custom_component.common import (MockConfigEntry, async_capture_events,
                                                          async_fire_time_changed)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import BAKE_ID, OTHER_BAKE_ID, bake_json, mock_bakes, step_json

NOW = "2026-11-09T18:50:00+00:00"  # step 2 of under_way() is due at 19:05


@pytest.fixture(autouse=True)
def frozen(freezer: FrozenDateTimeFactory) -> FrozenDateTimeFactory:
    freezer.move_to(NOW)
    return freezer


@pytest.fixture
def events(hass: HomeAssistant) -> list[Event]:
    captured: list[Event] = []
    for event_type in (EVENT_STEP_UPCOMING, EVENT_STEP_DUE, EVENT_STEP_LATE):
        captured_type = async_capture_events(hass, event_type)
        captured.append(captured_type)  # type: ignore[arg-type]
    return captured  # three lists, merged by fired()


def fired(events: list) -> list[tuple[str, int, str]]:
    """(event type, step position, time) for every step event so far, in order."""
    merged = [event for captured in events for event in captured]
    merged.sort(key=lambda event: event.time_fired)
    return [(event.event_type, event.data["position"], event.time_fired.strftime("%H:%M")) for event in merged]


def under_way(**step2) -> dict:
    """Step 1 checked off at 18:05; step 2 due at 19:05 (with any fields of step 2 replaced)."""
    bake = bake_json(projected_end="2026-11-10T02:05:00+00:00")
    bake["steps"] = [step_json(1, "2026-11-09T18:00:00+00:00", 3900, completed_at="2026-11-09T18:05:00+00:00"),
                     {**step_json(2, "2026-11-09T19:05:00+00:00", 25200), **step2},
                     step_json(3, "2026-11-10T02:05:00+00:00", 0)]
    return bake


async def new_answer(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entry: MockConfigEntry,
                     bakes: list[dict]) -> None:
    """Breadsheet's answer changes, and HA hears about it at once (a nudge, or a local check-off)."""
    aioclient_mock.clear_requests()
    mock_bakes(aioclient_mock, bakes, interval=60, etag='"v2"')
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()


async def set_up(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def until(hass: HomeAssistant, freezer: FrozenDateTimeFactory, when: str) -> None:
    """Let time run to `when` a minute at a time, firing timers and polls as they come due."""
    target = freezer.time_to_freeze.replace(hour=int(when[:2]), minute=int(when[3:5]), second=0)
    while freezer.time_to_freeze < target:
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()


async def test_upcoming_due_and_late_for_a_step_left_undone(
        hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
        frozen: FrozenDateTimeFactory, events: list) -> None:
    mock_bakes(aioclient_mock, [under_way()], interval=60)
    await set_up(hass, mock_config_entry)
    await until(hass, frozen, "19:20")
    assert fired(events) == [(EVENT_STEP_UPCOMING, 2, "19:03"), (EVENT_STEP_DUE, 2, "19:05"),
                             (EVENT_STEP_LATE, 2, "19:10")]


async def test_events_carry_what_a_notification_needs(
        hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
        frozen: FrozenDateTimeFactory) -> None:
    due = async_capture_events(hass, EVENT_STEP_DUE)
    mock_bakes(aioclient_mock, [under_way(note="Wet hands")], interval=60)
    await set_up(hass, mock_config_entry)
    await until(hass, frozen, "19:06")
    assert due[0].data == {
        "bake_id": BAKE_ID, "bake_name": "Weekend Loaf", "bake_path": "/bakes/abc123",
        "position": 2, "text": "Step 2", "note": "Wet hands", "scheduled_at": "2026-11-09T19:05:00+00:00",
        "hands_on_seconds": 600, "next_position": 3, "next_text": "Step 3",
        "next_scheduled_at": "2026-11-10T02:05:00+00:00", "steps_done": 1, "steps_total": 3,
    }


async def test_a_step_checked_off_early_stops_its_events(
        hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
        frozen: FrozenDateTimeFactory, events: list) -> None:
    mock_bakes(aioclient_mock, [under_way()], interval=60)
    await set_up(hass, mock_config_entry)
    await until(hass, frozen, "19:04")  # upcoming fired at 19:03

    await new_answer(hass, aioclient_mock, mock_config_entry, [under_way(completed_at="2026-11-09T19:04:00+00:00")])
    await until(hass, frozen, "19:20")
    assert fired(events) == [(EVENT_STEP_UPCOMING, 2, "19:03")]


async def test_a_moved_step_fires_at_its_new_time(
        hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
        frozen: FrozenDateTimeFactory, events: list) -> None:
    mock_bakes(aioclient_mock, [under_way()], interval=60)
    await set_up(hass, mock_config_entry)

    # Step 2 moves earlier, to 18:58: inside the window a calendar trigger has already read
    await new_answer(hass, aioclient_mock, mock_config_entry, [under_way(scheduled_at="2026-11-09T18:58:00+00:00")])
    await until(hass, frozen, "19:00")
    assert fired(events) == [(EVENT_STEP_UPCOMING, 2, "18:56"), (EVENT_STEP_DUE, 2, "18:58")]


async def test_a_new_bake_fires_without_waiting(
        hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
        frozen: FrozenDateTimeFactory, events: list) -> None:
    mock_bakes(aioclient_mock, [], interval=60)
    await set_up(hass, mock_config_entry)

    new = bake_json(OTHER_BAKE_ID, projected_end="2026-11-10T03:00:00+00:00")
    new["steps"][0] = step_json(1, "2026-11-09T18:53:00+00:00", 3600)
    await new_answer(hass, aioclient_mock, mock_config_entry, [new])
    await until(hass, frozen, "18:54")
    assert fired(events) == [(EVENT_STEP_UPCOMING, 1, "18:51"), (EVENT_STEP_DUE, 1, "18:53")]


async def test_same_minute_steps_fire_separately_in_order(
        hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
        frozen: FrozenDateTimeFactory, events: list) -> None:
    bake = under_way()
    bake["steps"][2] = step_json(3, "2026-11-09T19:05:00+00:00", 0)
    bake["steps"][1]["wait_chosen"] = 0
    mock_bakes(aioclient_mock, [bake], interval=60)
    await set_up(hass, mock_config_entry)
    await until(hass, frozen, "19:06")
    assert [(kind, position) for kind, position, _ in fired(events) if kind == EVENT_STEP_DUE] == [
        (EVENT_STEP_DUE, 2), (EVENT_STEP_DUE, 3)]


async def test_events_just_missed_still_fire_but_old_ones_dont(
        hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
        frozen: FrozenDateTimeFactory, events: list) -> None:
    frozen.move_to("2026-11-09T19:05:30+00:00")  # HA came up 30 seconds after step 2 was due
    mock_bakes(aioclient_mock, [under_way()], interval=60)
    await set_up(hass, mock_config_entry)
    assert [(kind, position) for kind, position, _ in fired(events)] == [(EVENT_STEP_DUE, 2)]


async def test_each_event_fires_once(
        hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
        frozen: FrozenDateTimeFactory, events: list) -> None:
    mock_bakes(aioclient_mock, [under_way()], interval=60)
    await set_up(hass, mock_config_entry)
    await until(hass, frozen, "19:06")
    coordinator = mock_config_entry.runtime_data.coordinator
    await coordinator.async_refresh()  # a reset right after the due time
    await hass.async_block_till_done()
    assert [kind for kind, _, _ in fired(events)].count(EVENT_STEP_DUE) == 1


async def test_the_lead_time_is_an_option(
        hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, frozen: FrozenDateTimeFactory,
        events: list, mock_config_entry: MockConfigEntry) -> None:
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, options={CONF_LEAD_MINUTES: 10})
    mock_bakes(aioclient_mock, [under_way()], interval=60)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    await until(hass, frozen, "18:56")
    assert fired(events) == [(EVENT_STEP_UPCOMING, 2, "18:55")]


async def test_a_lead_time_of_zero_turns_upcoming_off(
        hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, frozen: FrozenDateTimeFactory,
        events: list, mock_config_entry: MockConfigEntry) -> None:
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_config_entry, options={CONF_LEAD_MINUTES: 0})
    mock_bakes(aioclient_mock, [under_way()], interval=60)
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    await until(hass, frozen, "19:06")
    assert [kind for kind, _, _ in fired(events)] == [EVENT_STEP_DUE]


async def test_unloading_stops_the_events(
        hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
        frozen: FrozenDateTimeFactory, events: list) -> None:
    mock_bakes(aioclient_mock, [under_way()], interval=60)
    await set_up(hass, mock_config_entry)
    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await until(hass, frozen, "19:20")
    assert fired(events) == []


class TestBakeChanged:
    async def test_a_new_bake_and_each_check_off_are_announced(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        from custom_components.breadsheet.const import EVENT_BAKE_CHANGED
        changed = async_capture_events(hass, EVENT_BAKE_CHANGED)
        mock_bakes(aioclient_mock, [under_way()], interval=60)
        await set_up(hass, mock_config_entry)
        assert changed[0].data == {
            "bake_id": BAKE_ID, "bake_name": "Weekend Loaf", "bake_path": "/bakes/abc123",
            "next_position": 2, "next_text": "Step 2", "next_scheduled_at": "2026-11-09T19:05:00+00:00",
            "steps_done": 1, "steps_total": 3}

        await new_answer(hass, aioclient_mock, mock_config_entry, [under_way(completed_at="2026-11-09T18:58:00+00:00")])
        assert [(e.data["next_position"], e.data["steps_done"]) for e in changed] == [(2, 1), (3, 2)]

    async def test_unchanged_polls_announce_nothing(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, mock_config_entry: MockConfigEntry,
            frozen: FrozenDateTimeFactory) -> None:
        from custom_components.breadsheet.const import EVENT_BAKE_CHANGED
        changed = async_capture_events(hass, EVENT_BAKE_CHANGED)
        mock_bakes(aioclient_mock, [under_way()], interval=60)
        await set_up(hass, mock_config_entry)
        await until(hass, frozen, "18:55")  # several polls with the same answer
        assert len(changed) == 1

    async def test_a_finished_bake_announces_no_next_step(
            self, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker,
            mock_config_entry: MockConfigEntry) -> None:
        from custom_components.breadsheet.const import EVENT_BAKE_CHANGED
        changed = async_capture_events(hass, EVENT_BAKE_CHANGED)
        mock_bakes(aioclient_mock, [under_way()], interval=60)
        await set_up(hass, mock_config_entry)
        done = under_way(completed_at="2026-11-09T18:58:00+00:00")
        done["steps"][2]["completed_at"] = "2026-11-09T18:59:00+00:00"
        await new_answer(hass, aioclient_mock, mock_config_entry, [done])
        assert (changed[-1].data["next_position"], changed[-1].data["steps_done"]) == (None, 3)

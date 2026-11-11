"""Tests for the bake notification blueprint, run as a real automation."""

from datetime import timedelta
from pathlib import Path
import shutil
from typing import Any

import pytest

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import async_mock_service

BLUEPRINT = Path(__file__).parent.parent / "blueprints/automation/breadsheet/bake_notifications.yaml"
BAKE_ID = "0196f2a0-0000-7000-8000-0000000b0001"


def step_event(position: int = 2, text: str = "Mix", scheduled_at: str = "2026-11-09T19:05:00+00:00") -> dict:
    """What the integration's step events carry (step_events.step_event_data)."""
    return {"bake_id": BAKE_ID, "bake_name": "Weekend Loaf", "bake_path": "/bakes/abc123", "position": position,
            "text": text, "note": None, "scheduled_at": scheduled_at, "hands_on_seconds": 600,
            "next_position": position + 1, "next_text": "Bake", "next_scheduled_at": None,
            "steps_done": position - 1, "steps_total": 3}


@pytest.fixture
async def notified(hass: HomeAssistant) -> list[ServiceCall]:
    return async_mock_service(hass, "notify", "mobile_app_phone")


@pytest.fixture
async def completed(hass: HomeAssistant) -> list[ServiceCall]:
    return async_mock_service(hass, "breadsheet", "complete_step")


async def use_blueprint(hass: HomeAssistant, **inputs: Any) -> None:
    target = Path(hass.config.path("blueprints/automation/breadsheet/bake_notifications.yaml"))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BLUEPRINT, target)
    assert await async_setup_component(hass, "automation", {"automation": {
        "alias": "Bake notifications",
        "use_blueprint": {"path": "breadsheet/bake_notifications.yaml",
                          "input": {"notify_services": ["notify.mobile_app_phone"], **inputs}},
    }})
    await hass.async_block_till_done()
    assert hass.states.get("automation.bake_notifications").state == "on"


async def fire(hass: HomeAssistant, event_type: str, data: dict) -> None:
    hass.bus.async_fire(event_type, data)
    await hass.async_block_till_done()


def local_time(iso: str) -> str:
    return dt_util.as_local(dt_util.parse_datetime(iso)).strftime("%-I:%M %p")


async def test_a_due_step_notifies_with_a_done_button(hass: HomeAssistant, notified: list) -> None:
    await use_blueprint(hass)
    await fire(hass, "breadsheet_step_due", step_event())
    assert len(notified) == 1
    call = notified[0].data
    assert (call["title"], call["message"]) == ("Weekend Loaf", "Now: Mix")
    assert call["data"]["subtitle"] == "Step 2 of 3"
    assert call["data"]["tag"] == f"breadsheet_{BAKE_ID}_2"  # the due one replaces the warning
    assert call["data"]["actions"] == [{"action": f"BREADSHEET_DONE|{BAKE_ID}|2", "title": "Done"}]
    assert call["data"]["url"] == call["data"]["clickAction"] == f"/breadsheet/{BAKE_ID}"


async def test_the_warning_says_when(hass: HomeAssistant, notified: list) -> None:
    await use_blueprint(hass, send_upcoming=True)
    await fire(hass, "breadsheet_step_upcoming", step_event())
    assert notified[0].data["message"] == f"At {local_time('2026-11-09T19:05:00+00:00')}: Mix"


async def test_the_late_reminder(hass: HomeAssistant, notified: list) -> None:
    await use_blueprint(hass)
    await fire(hass, "breadsheet_step_late", step_event())
    assert notified[0].data["message"] == f"Still to do (due {local_time('2026-11-09T19:05:00+00:00')}): Mix"


async def test_warnings_are_off_unless_turned_on(hass: HomeAssistant, notified: list) -> None:
    await use_blueprint(hass, live_activities=False)
    await fire(hass, "breadsheet_step_upcoming", step_event())
    assert notified == []


@pytest.mark.parametrize(("inputs", "event_type"), [({"send_upcoming": False}, "breadsheet_step_upcoming"),
                                                    ({"send_late": False}, "breadsheet_step_late")])
async def test_warnings_and_reminders_can_be_turned_off(hass: HomeAssistant, notified: list, inputs: dict,
                                                       event_type: str) -> None:
    await use_blueprint(hass, **inputs)
    await fire(hass, event_type, step_event())
    assert notified == []


async def test_every_phone_is_notified_even_if_one_fails(hass: HomeAssistant, notified: list) -> None:
    async def broken(call: ServiceCall) -> None:
        raise HomeAssistantError("phone unreachable")
    hass.services.async_register("notify", "mobile_app_old_phone", broken)
    await use_blueprint(hass, notify_services=["notify.mobile_app_old_phone", "notify.mobile_app_phone"])
    await fire(hass, "breadsheet_step_due", step_event())
    assert len(notified) == 1


async def test_same_minute_steps_arrive_separately_in_order(hass: HomeAssistant, notified: list) -> None:
    await use_blueprint(hass)
    started = dt_util.utcnow()
    hass.bus.async_fire("breadsheet_step_due", step_event(2, "Mix"))
    hass.bus.async_fire("breadsheet_step_due", step_event(3, "Shape"))
    await hass.async_block_till_done()  # runs both, queued, with the real pause between them
    assert [call.data["message"] for call in notified] == ["Now: Mix", "Now: Shape"]
    assert dt_util.utcnow() - started >= timedelta(seconds=1.5)


async def test_done_checks_the_step_off(hass: HomeAssistant, completed: list) -> None:
    await use_blueprint(hass)
    await fire(hass, "mobile_app_notification_action", {"action": f"BREADSHEET_DONE|{BAKE_ID}|2"})
    assert [(call.data["bake_id"], call.data["position"]) for call in completed] == [(BAKE_ID, 2)]


@pytest.mark.parametrize("action", ["SOMETHING_ELSE", "BREADSHEET_DONE|only-two", "", None])
async def test_other_notification_buttons_are_ignored(hass: HomeAssistant, completed: list, notified: list,
                                                      action: str | None) -> None:
    await use_blueprint(hass)
    await fire(hass, "mobile_app_notification_action", {"action": action} if action is not None else {})
    assert completed == [] and notified == []


async def test_recipe_text_is_shown_never_run_as_a_template(hass: HomeAssistant, notified: list) -> None:
    await use_blueprint(hass)
    await fire(hass, "breadsheet_step_due", step_event(text="{{ states('sensor.secret') }}"))
    assert notified[0].data["message"] == "Now: {{ states('sensor.secret') }}"


def in_minutes(minutes: float) -> str:
    return (dt_util.utcnow() + timedelta(minutes=minutes)).isoformat()


def changed(next_position: int | None = 2, text: str | None = "Mix", at: str | None = None, done: int = 1) -> dict:
    """What breadsheet_bake_changed carries (step_events.bake_changed_data)."""
    return {"bake_id": BAKE_ID, "bake_name": "Weekend Loaf", "bake_path": "/bakes/abc123",
            "next_position": next_position, "next_text": text,
            "next_scheduled_at": at if at is not None or next_position is None else in_minutes(30),
            "steps_done": done, "steps_total": 3}


def cards(notified: list) -> list[dict]:
    """The live_update and clear_notification calls, as (message, data)."""
    return [call.data for call in notified if call.data.get("data", {}).get("live_update")
            or call.data.get("message") == "clear_notification"]


class TestLiveActivities:
    async def test_on_unless_turned_off(self, hass: HomeAssistant, notified: list) -> None:
        await use_blueprint(hass)
        await fire(hass, "breadsheet_bake_changed", changed(at=in_minutes(30)))
        assert len(cards(notified)) == 1

    async def test_turned_off_sends_nothing(self, hass: HomeAssistant, notified: list) -> None:
        await use_blueprint(hass, live_activities=False)
        await fire(hass, "breadsheet_bake_changed", changed())
        await fire(hass, "breadsheet_bake_ended", {"bake_id": BAKE_ID})
        assert notified == []

    async def test_a_card_counts_down_to_the_next_step(self, hass: HomeAssistant, notified: list) -> None:
        await use_blueprint(hass, live_activities=True)
        at = in_minutes(30)
        await fire(hass, "breadsheet_bake_changed", changed(at=at))
        (card,) = cards(notified)
        assert (card["title"], card["message"]) == ("Weekend Loaf", "Mix")
        data = card["data"]
        assert data["tag"] == f"breadsheet_{BAKE_ID}"
        assert (data["chronometer"], data["when"], data["when_relative"]) == (
            True, int(dt_util.parse_datetime(at).timestamp()), False)
        assert (data["progress"], data["progress_max"], data["critical_text"]) == (1, 3, "Mix 2/3")
        assert (data["silent"], data["alert_once"]) == (True, True)
        assert data["url"] == data["clickAction"] == f"/breadsheet/{BAKE_ID}"

    async def test_the_label_is_short(self, hass: HomeAssistant, notified: list) -> None:
        await use_blueprint(hass, live_activities=True)
        await fire(hass, "breadsheet_bake_changed", changed(text="Stretch-and-folds every 30 minutes"))
        assert cards(notified)[0]["data"]["critical_text"] == "Stretch-and- 2/3"

    async def test_it_ends_before_a_long_wait(self, hass: HomeAssistant, notified: list) -> None:
        await use_blueprint(hass, live_activities=True)
        await fire(hass, "breadsheet_bake_changed", changed(at=in_minutes(5 * 60)))
        assert cards(notified) == [{"message": "clear_notification", "data": {"tag": f"breadsheet_{BAKE_ID}"}}]

    async def test_and_comes_back_with_the_warning_before_the_next_step(self, hass: HomeAssistant,
                                                                         notified: list) -> None:
        await use_blueprint(hass, live_activities=True, send_upcoming=False)
        await fire(hass, "breadsheet_step_upcoming", step_event(position=3, text="Bake", scheduled_at=in_minutes(2)))
        assert [card["message"] for card in cards(notified)] == ["Bake"]
        assert len(notified) == 1  # the card only: the warning itself is turned off

    async def test_steps_due_now_get_no_update(self, hass: HomeAssistant, notified: list) -> None:
        await use_blueprint(hass, live_activities=True)
        await fire(hass, "breadsheet_bake_changed", changed(at=in_minutes(0.5)))
        assert notified == []

    async def test_a_bake_with_nothing_left_is_cleared(self, hass: HomeAssistant, notified: list) -> None:
        await use_blueprint(hass, live_activities=True)
        await fire(hass, "breadsheet_bake_changed", changed(next_position=None, text=None, done=3))
        assert [card["message"] for card in cards(notified)] == ["clear_notification"]

    async def test_an_ended_bake_is_cleared(self, hass: HomeAssistant, notified: list) -> None:
        await use_blueprint(hass, live_activities=True)
        await fire(hass, "breadsheet_bake_ended", {"bake_id": BAKE_ID, "outcome": "canceled"})
        assert cards(notified) == [{"message": "clear_notification", "data": {"tag": f"breadsheet_{BAKE_ID}"}}]

    async def test_the_warning_and_the_card_both_go_out(self, hass: HomeAssistant, notified: list) -> None:
        await use_blueprint(hass, send_upcoming=True, live_activities=True)
        await fire(hass, "breadsheet_step_upcoming", step_event(scheduled_at=in_minutes(2)))
        assert len(notified) == 2
        assert notified[0].data["message"].startswith("At ")
        assert notified[1].data["data"]["live_update"] is True

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
    await use_blueprint(hass)
    await fire(hass, "breadsheet_step_upcoming", step_event())
    assert notified[0].data["message"] == f"At {local_time('2026-11-09T19:05:00+00:00')}: Mix"


async def test_the_late_reminder(hass: HomeAssistant, notified: list) -> None:
    await use_blueprint(hass)
    await fire(hass, "breadsheet_step_late", step_event())
    assert notified[0].data["message"] == f"Still to do (due {local_time('2026-11-09T19:05:00+00:00')}): Mix"


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

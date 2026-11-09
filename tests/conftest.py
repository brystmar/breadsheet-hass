"""Shared fixtures for the Breadsheet tests."""

from collections.abc import Generator
from unittest.mock import patch

import pytest

from homeassistant.const import CONF_TOKEN

from custom_components.breadsheet.const import CONF_API_URL, CONF_CONNECTION_ID, DEFAULT_API_URL, DOMAIN
from pytest_homeassistant_custom_component.common import MockConfigEntry

DEVICES_URL = f"{DEFAULT_API_URL}/api/v2/devices"
BAKES_URL = f"{DEFAULT_API_URL}/api/v2/bakes?status=active&alert=fancy"
WEBHOOK_URL = f"{DEFAULT_API_URL}/api/v2/device/webhook"
BAKE_ID = "0196f2a0-0000-7000-8000-0000000b0001"
OTHER_BAKE_ID = "0196f2a0-0000-7000-8000-0000000b0002"
USER_ID = "0196f2a0-0000-7000-8000-000000000001"
OTHER_USER_ID = "0196f2a0-0000-7000-8000-000000000002"
TOKEN = "test-device-token"


def pairing_response(user_id: str = USER_ID, username: str = "baker", token: str = TOKEN,
                     connection_id: str = "0196f2a0-0000-7000-8000-00000000c001") -> dict:
    """The shape of a real 201 from POST /api/v2/devices."""
    return {
        "token": token,
        "connection": {"id": connection_id, "label": "Home", "created_at": "2026-11-09T18:00:00+00:00",
                       "last_used_at": None,
                       "scopes": ["recipes:read", "recipes:write", "bakes:read", "bakes:write"]},
        "user": {"id": user_id, "username": username, "created_at": "2026-11-01T00:00:00+00:00",
                 "is_admin": False},
    }


def step_json(position: int, scheduled_at: str | None, wait: int, *, completed_at: str | None = None,
              skipped: bool = False) -> dict:
    """The shape of a real `bake step` from breadsheet (docs/api-v2.md)."""
    return {
        "position": position, "text": f"Step {position}", "note": None,
        "wait_min": wait, "wait_max": wait, "wait_chosen": wait, "wait_outside_range": False,
        "active_seconds": None, "needs_presence": None,
        "hands_on": {"seconds": 600, "estimated": True, "needs_presence": True},
        "skipped": skipped, "skipped_at": None, "skipped_at_local": None,
        "scheduled_at": scheduled_at, "scheduled_at_local": scheduled_at,
        "completed_at": completed_at, "completed_at_local": completed_at,
    }


def bake_json(bake_id: str = BAKE_ID, *, projected_end: str = "2026-11-10T02:00:00+00:00",
              name: str = "Weekend Loaf") -> dict:
    """The shape of a real active Fancy `bake` from breadsheet (docs/api-v2.md)."""
    return {
        "id": bake_id, "suffix": "abc123", "path": "/bakes/abc123", "name": name,
        "recipe_id": "0196f2a0-0000-7000-8000-0000000a0001", "recipe_path": "/recipes/weekend-loaf",
        "recipe_deleted": False, "anchor_type": "start", "anchor_time": "2026-11-09T18:00:00+00:00",
        "time_zone": "America/Los_Angeles", "alert_mode": "fancy", "status": "not_started", "outcome": None,
        "created_via": "web", "started_at": "2026-11-09T17:50:00+00:00", "completed_at": None,
        "projected_end": projected_end, "projected_end_local": projected_end,
        "steps": [step_json(1, "2026-11-09T18:00:00+00:00", 3600),
                  step_json(2, "2026-11-09T19:00:00+00:00", 25200),
                  step_json(3, "2026-11-10T02:00:00+00:00", 0)],
    }


def mock_bakes(aioclient_mock, bakes: list[dict] | None = None, *, interval: int | None = 900,
               etag: str = '"etag-1"', status: int = 200, **kwargs) -> None:
    """Breadsheet's answer to HA's poll, with its real headers."""
    headers = {"ETag": etag}
    if interval is not None:
        headers["X-Breadsheet-Poll-Interval"] = str(interval)
    if status == 200:
        kwargs.setdefault("json", {"bakes": bakes or []})
    aioclient_mock.get(BAKES_URL, status=status, headers=headers, **kwargs)
    mock_webhook(aioclient_mock)


def mock_webhook(aioclient_mock, **kwargs) -> None:
    """Breadsheet's answer to setting or clearing this HA's webhook (204 unless told otherwise)."""
    kwargs.setdefault("status", 204)
    aioclient_mock.put(WEBHOOK_URL, **kwargs)
    aioclient_mock.delete(WEBHOOK_URL, **kwargs)


def error_body(code: str, message: str = "Nope.") -> dict:
    return {"code": code, "message": message}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="baker",
        unique_id=USER_ID,
        data={CONF_API_URL: DEFAULT_API_URL, CONF_TOKEN: TOKEN,
              CONF_CONNECTION_ID: "0196f2a0-0000-7000-8000-00000000c001"},
    )


@pytest.fixture
def mock_setup_entry() -> Generator[None]:
    """Keep the config flow tests from setting up the entry they create."""
    with patch("custom_components.breadsheet.async_setup_entry", return_value=True):
        yield

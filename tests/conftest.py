"""Shared fixtures for the Breadsheet tests."""

from collections.abc import Generator
from unittest.mock import patch

import pytest

from homeassistant.const import CONF_TOKEN

from custom_components.breadsheet.const import CONF_API_URL, CONF_CONNECTION_ID, DEFAULT_API_URL, DOMAIN
from pytest_homeassistant_custom_component.common import MockConfigEntry

DEVICES_URL = f"{DEFAULT_API_URL}/api/v2/devices"
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

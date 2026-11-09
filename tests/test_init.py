"""Tests for setting up and unloading a config entry, and its diagnostics."""

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import TOKEN, bake_json, mock_bakes


async def test_setup_and_unload(hass: HomeAssistant, mock_config_entry: MockConfigEntry,
                                aioclient_mock: AiohttpClientMocker) -> None:
    mock_bakes(aioclient_mock)
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


async def test_diagnostics_never_include_the_token(hass: HomeAssistant, hass_client: ClientSessionGenerator,
                                                   mock_config_entry: MockConfigEntry,
                                                   aioclient_mock: AiohttpClientMocker) -> None:
    mock_bakes(aioclient_mock, [bake_json()], interval=60)
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, mock_config_entry)

    assert TOKEN not in str(diagnostics)
    assert diagnostics["entry_data"]["token"] == "**REDACTED**"
    assert diagnostics["entry_data"]["api_url"] == "**REDACTED**"
    assert diagnostics["api_url_is_default"] is True
    assert diagnostics["coordinator"]["active_fancy_bakes"] == 1
    assert diagnostics["coordinator"]["update_interval_seconds"] == 60
    assert "Weekend Loaf" not in str(diagnostics)  # recipe text stays out

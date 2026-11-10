"""The "Breadsheet" sidebar panel (breadsheet-hass#5; spec: HA integration, Sidebar panel).

The panel shows breadsheet's bake view in a frame. Embedded pages can't rely on breadsheet's session cookie
(Safari and the companion app block third-party cookies), so the panel's script asks this integration, over HA's
websocket, for a fresh URL with a short-lived panel token in its fragment (`POST /api/v2/panel-tokens`). Panel
tokens work only for bakes, last 12 hours, and stop when the connection is revoked. Notification and Live
Activity taps open /breadsheet/<bake id>, which opens that bake.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import probatio

from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.loader import async_get_integration

from .api import BreadsheetApiError, BreadsheetError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PANEL_URL_PATH = "breadsheet"
WEBCOMPONENT = "breadsheet-panel"
STATIC_URL = "/breadsheet_static/breadsheet-panel.js"
DATA_STATIC = f"{DOMAIN}_panel_static"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Add the sidebar panel, if it isn't there already."""
    if PANEL_URL_PATH in hass.data.get(frontend.DATA_PANELS, {}):
        return
    if not hass.data.get(DATA_STATIC):  # static paths can't be removed, so they're registered once per run
        await hass.http.async_register_static_paths([StaticPathConfig(
            STATIC_URL, str(Path(__file__).parent / "frontend" / "breadsheet-panel.js"), cache_headers=False)])
        hass.data[DATA_STATIC] = True
    version = (await async_get_integration(hass, DOMAIN)).version
    await panel_custom.async_register_panel(
        hass, frontend_url_path=PANEL_URL_PATH, webcomponent_name=WEBCOMPONENT, sidebar_title="Breadsheet",
        sidebar_icon="mdi:bread-slice", module_url=f"{STATIC_URL}?v={version}", require_admin=False)


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, websocket_panel_url)


@websocket_api.websocket_command({
    probatio.Required("type"): "breadsheet/panel_url",
    probatio.Optional("bake_id"): str,
})
@websocket_api.async_response
async def websocket_panel_url(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                              msg: dict[str, Any]) -> None:
    """A fresh panel URL: for the account that has `bake_id`, or the first account set up."""
    entries = hass.config_entries.async_loaded_entries(DOMAIN)
    bake_id = msg.get("bake_id")
    entry = next((e for e in entries if bake_id and e.runtime_data.coordinator.data
                  and bake_id in e.runtime_data.coordinator.data.bakes), entries[0] if entries else None)
    if entry is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "Breadsheet isn't set up.")
        return
    try:
        url = await entry.runtime_data.client.panel_url(bake_id)
    except BreadsheetApiError as err:
        if err.code == "not_found" and bake_id:
            url = await _try(entry, None, connection, msg)  # the bake ended: show the list instead
            if url is None:
                return
        else:
            connection.send_error(msg["id"], websocket_api.ERR_HOME_ASSISTANT_ERROR,
                                  "Breadsheet refused the panel. Try pairing again.")
            return
    except BreadsheetError:
        connection.send_error(msg["id"], websocket_api.ERR_HOME_ASSISTANT_ERROR,
                              "Breadsheet can't be reached right now. Try again soon.")
        return
    connection.send_result(msg["id"], {"url": url})


async def _try(entry, bake_id, connection, msg) -> str | None:
    try:
        return await entry.runtime_data.client.panel_url(bake_id)
    except BreadsheetError:
        connection.send_error(msg["id"], websocket_api.ERR_HOME_ASSISTANT_ERROR,
                              "Breadsheet can't be reached right now. Try again soon.")
        return None

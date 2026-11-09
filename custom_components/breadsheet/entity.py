"""Base entities: the account device, and one device per active bake."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_API_URL, DEFAULT_API_URL, DOMAIN, WEB_URL
from .coordinator import BreadsheetCoordinator
from .models import Bake


def account_device(coordinator: BreadsheetCoordinator) -> DeviceInfo:
    """The breadsheet account this config entry paired with."""
    entry = coordinator.config_entry
    return DeviceInfo(identifiers={(DOMAIN, str(entry.unique_id))}, name=f"Breadsheet ({entry.title})",
                      manufacturer="Breadsheet", model="Account", entry_type=DeviceEntryType.SERVICE)


class BreadsheetEntity(CoordinatorEntity[BreadsheetCoordinator]):
    """Every Breadsheet entity: named by translation, and says when its data last synced."""

    _attr_has_entity_name = True

    def _attributes(self) -> dict[str, Any]:
        state = self.coordinator.data
        return {"last_synced": state.last_synced.isoformat() if state and state.last_synced else None}


class BreadsheetBakeEntity(BreadsheetEntity):
    """An entity of one active bake, on that bake's device."""

    def __init__(self, coordinator: BreadsheetCoordinator, bake: Bake, key: str) -> None:
        super().__init__(coordinator)
        self.bake_id = bake.id
        self._attr_unique_id = f"{bake.id}_{key}"
        self._attr_translation_key = key
        on_production = coordinator.config_entry.data.get(CONF_API_URL) == DEFAULT_API_URL
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, bake.id)}, name=bake.name, manufacturer="Breadsheet", model="Bake",
            entry_type=DeviceEntryType.SERVICE, via_device=(DOMAIN, str(coordinator.config_entry.unique_id)),
            configuration_url=f"{WEB_URL}{bake.path}" if on_production and bake.path else None)

    @property
    def bake(self) -> Bake | None:
        state = self.coordinator.data
        return state.bakes.get(self.bake_id) if state else None

    @property
    def available(self) -> bool:
        """Up through failed polls until the bake's projected end (not CoordinatorEntity's last_update_success)."""
        bake = self.bake
        return bake is not None and self.coordinator.bake_available(bake)

    def _attributes(self) -> dict[str, Any]:
        return {"bake_id": self.bake_id, **super()._attributes()}

"""The breadsheet.complete_step action, which the blueprint's Done button calls."""

from __future__ import annotations

import probatio

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .const import DOMAIN

SERVICE_COMPLETE_STEP = "complete_step"
ATTR_BAKE_ID = "bake_id"
ATTR_POSITION = "position"

COMPLETE_STEP_SCHEMA = probatio.Schema({
    probatio.Required(ATTR_BAKE_ID): cv.string,
    probatio.Required(ATTR_POSITION): probatio.All(probatio.Coerce(int), probatio.Range(min=1)),
})


@callback
def async_setup_services(hass: HomeAssistant) -> None:

    async def complete_step(call: ServiceCall) -> None:
        """Check off a step now. Shown at once; sent to breadsheet, or queued until it can be."""
        completed_at = dt_util.utcnow()  # when it was tapped, not when breadsheet hears about it
        bake_id, position = call.data[ATTR_BAKE_ID], call.data[ATTR_POSITION]
        for entry in hass.config_entries.async_loaded_entries(DOMAIN):
            state = entry.runtime_data.coordinator.data
            if state is not None and (bake := state.bakes.get(bake_id)) is not None:
                break
        else:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_bake",
                                         translation_placeholders={"bake_id": bake_id})
        step = next((step for step in bake.steps if step.position == position), None)
        if step is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_step",
                                         translation_placeholders={"position": str(position), "bake": bake.name})
        if step.done:
            return  # first write wins, as on breadsheet
        await entry.runtime_data.checkoffs.async_check_off(bake_id, position, completed_at)

    hass.services.async_register(DOMAIN, SERVICE_COMPLETE_STEP, complete_step, schema=COMPLETE_STEP_SCHEMA)

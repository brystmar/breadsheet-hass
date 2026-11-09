"""Config flow for Breadsheet: pair with a code from breadsheet.com.

The user's breadsheet key never enters HA. The flow trades a one-time pairing code (`XXXX-XXXX`, ten
minutes) for a device token at `POST /api/v2/devices` and stores only the token.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
import os
from typing import Any

import probatio

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_TOKEN

from . import async_create_client
from .api import BreadsheetApiError, BreadsheetConnectionError, BreadsheetError, Pairing
from .const import (CONF_API_URL, CONF_CONNECTION_ID, CONF_PAIRING_CODE, DEFAULT_API_URL, DOMAIN, ENV_API_URL,
                    LABEL_MAX_LENGTH, PROFILE_URL)

_LOGGER = logging.getLogger(__name__)

PAIRING_SCHEMA = probatio.Schema({probatio.Required(CONF_PAIRING_CODE): str})


class BreadsheetConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pair a breadsheet account. One config entry per breadsheet account, keyed on its user id."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a pairing code and exchange it for a device token."""
        errors: dict[str, str] = {}
        if user_input is not None:
            api_url = self._api_url()
            pairing = await self._pair(api_url, user_input[CONF_PAIRING_CODE], errors)
            if pairing is not None:
                await self.async_set_unique_id(pairing.user_id)
                # The code is already spent, so pairing an account twice leaves an extra connection on
                # breadsheet; the abort message tells the user to revoke it from their profile.
                self._abort_if_unique_id_configured(description_placeholders={"profile_url": PROFILE_URL})
                return self.async_create_entry(title=pairing.username, data=self._entry_data(api_url, pairing))

        return self.async_show_form(step_id="user", data_schema=PAIRING_SCHEMA, errors=errors,
                                    description_placeholders={"profile_url": PROFILE_URL})

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """The device token stopped working (revoked on breadsheet.com): pair again."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a new pairing code for the same breadsheet account."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            api_url = entry.data[CONF_API_URL]
            pairing = await self._pair(api_url, user_input[CONF_PAIRING_CODE], errors)
            if pairing is not None:
                await self.async_set_unique_id(pairing.user_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account",
                                                  description_placeholders={"username": entry.title,
                                                                            "profile_url": PROFILE_URL})
                return self.async_update_reload_and_abort(entry, title=pairing.username,
                                                          data=self._entry_data(api_url, pairing))

        return self.async_show_form(step_id="reauth_confirm", data_schema=PAIRING_SCHEMA, errors=errors,
                                    description_placeholders={"username": entry.title, "profile_url": PROFILE_URL})

    @staticmethod
    def _api_url() -> str:
        """Production, unless the development override is set (see ENV_API_URL)."""
        return os.environ.get(ENV_API_URL, "").strip() or DEFAULT_API_URL

    @staticmethod
    def _entry_data(api_url: str, pairing: Pairing) -> dict[str, Any]:
        return {CONF_API_URL: api_url, CONF_TOKEN: pairing.token, CONF_CONNECTION_ID: pairing.connection_id}

    def _label(self) -> str:
        """What breadsheet lists this HA as on the user's profile: HA's own name."""
        name = (self.hass.config.location_name or "").strip() or "Home Assistant"
        return name[:LABEL_MAX_LENGTH]

    async def _pair(self, api_url: str, code: str, errors: dict[str, str]) -> Pairing | None:
        """Exchange the code, or fill in `errors` and return None."""
        client = await async_create_client(self.hass, api_url)
        try:
            return await client.pair(code.strip(), self._label())
        except BreadsheetApiError as err:
            if err.code == "pairing_code_invalid":
                errors[CONF_PAIRING_CODE] = "invalid_code"
            elif err.code == "rate_limited":
                errors["base"] = "rate_limited"
            else:
                _LOGGER.warning("Breadsheet refused pairing: %s", err.code)
                errors["base"] = "unknown"
        except BreadsheetConnectionError as err:
            _LOGGER.debug("Couldn't reach breadsheet to pair: %s", err)
            errors["base"] = "cannot_connect"
        except BreadsheetError:
            _LOGGER.exception("Unexpected response while pairing")
            errors["base"] = "unknown"
        return None

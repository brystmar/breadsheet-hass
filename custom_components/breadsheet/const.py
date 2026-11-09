"""Constants for the Breadsheet integration."""

from typing import Final

DOMAIN: Final = "breadsheet"

# The production API. Every call goes to `<api_url>/api/v2/...`.
DEFAULT_API_URL: Final = "https://api.breadsheet.com"

# For development only: set this environment variable on the HA host before adding the integration to
# pair against another breadsheet backend (e.g. a local one). The URL is saved in the config entry, so
# later calls keep using it even if the variable goes away.
ENV_API_URL: Final = "BREADSHEET_API_URL"

# Where people get a pairing code
PROFILE_URL: Final = "https://www.breadsheet.com/profile"

# Fired when a bake leaves the active Fancy list (completed, canceled, expired, or switched to Simple), with
# `bake_id`. The blueprint clears that bake's Live Activity on it.
EVENT_BAKE_ENDED: Final = "breadsheet_bake_ended"

# Config entry data
CONF_API_URL: Final = "api_url"
CONF_CONNECTION_ID: Final = "connection_id"
CONF_PAIRING_CODE: Final = "pairing_code"

# The label breadsheet shows in the user's connections list is capped at this many characters
LABEL_MAX_LENGTH: Final = 60

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

# Step events, timed by the integration from the current schedule (spec: HA integration, Entities). Calendar
# triggers can't do this: they read events in 15-minute windows and never re-read them.
EVENT_STEP_UPCOMING: Final = "breadsheet_step_upcoming"
EVENT_STEP_DUE: Final = "breadsheet_step_due"
EVENT_STEP_LATE: Final = "breadsheet_step_late"
# Fired when a bake first appears, or its next step, that step's time, or its progress changes (a check-off, a
# skip, a new wait): what a Live Activity shows. Not fired for ended bakes; breadsheet_bake_ended covers those.
EVENT_BAKE_CHANGED: Final = "breadsheet_bake_changed"

# Options
CONF_LEAD_MINUTES: Final = "lead_minutes"
DEFAULT_LEAD_MINUTES: Final = 2
MAX_LEAD_MINUTES: Final = 60

# The bake page for a bake path (only for the production API; a dev backend has no matching site)
WEB_URL: Final = "https://www.breadsheet.com"

# Config entry data
CONF_API_URL: Final = "api_url"
CONF_CONNECTION_ID: Final = "connection_id"
CONF_PAIRING_CODE: Final = "pairing_code"
CONF_WEBHOOK_ID: Final = "webhook_id"

# The label breadsheet shows in the user's connections list is capped at this many characters
LABEL_MAX_LENGTH: Final = 60

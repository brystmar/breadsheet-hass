# breadsheet-hass — Claude Context

## Project Purpose
A Home Assistant custom integration for breadsheet.com, a recipe scheduling tool for
multi-day bakes like sourdough. It pulls a user's active Fancy bakes from the breadsheet
API and turns them into HA entities, notifications with a Done button, and iOS Live
Activities. From M4 it also registers a Breadsheet LLM API, so AI assistants (Claude,
ChatGPT, HA Assist) can plan bakes, check off steps, and import recipes through HA.

Distributed through the Home Assistant Marketplace (the built-in successor to HACS as of
HA 2026.11). This is a custom integration, not an add-on.

## Companion Repos
- **Backend** (`brystmar/breadsheet`): Python/Flask API at `api.breadsheet.com`. This
  integration talks only to its `/api/v2` endpoints.
- **Frontend** (`brystmar/breadsheet-ui`): React + Vite UI at breadsheet.com. The HA
  sidebar panel embeds its bake view.
- **This repo** (`brystmar/breadsheet-hass`): the HA integration and its blueprint.

## Second Rise spec
The source of truth is `../breadsheet/docs/second-rise.md`, in the sibling
checkout. Read it before planning or executing any work in this repo; the sections on
the HA integration, resilience, API v2, and AI agents matter most here. Never copy it
into this repo; if it needs changes, say so and stop.

## Current Status
- breadsheet-hass#1: the scaffold, Marketplace metadata (`hacs.json`, `brand/`), release packaging, and the
  pairing config flow with reauth and diagnostics.
- breadsheet-hass#2: `coordinator.py` polls active Fancy bakes with ETags at breadsheet's suggested interval
  (60 to 1800 seconds), keeps the last good bakes through outages (`bake_available()` for entities), saves them
  with `Store` so a restart during an outage carries on, and fires `breadsheet_bake_ended`. Setup keeps a
  listener of its own, since DataUpdateCoordinator only polls while something listens.
- breadsheet-hass#3: a "Bakes" calendar on the account device; one device per active bake (removed when it
  ends) with current step, next step, next step time, and progress sensors; and step events
  (`breadsheet_step_upcoming`, `_due`, `_late`) timed by `step_events.py`, with the lead time as an option.
  Don't build alerts on calendar triggers: they read events 15 minutes at a time and never re-read them.
  `DataUpdateCoordinator` skips listeners after a second failed poll in a row, so the coordinator sets its own
  timer for the next projected end so entities can go unavailable during an outage.
- breadsheet-hass#4: `breadsheet.complete_step` (`services.py`, registered in `async_setup`) records the tap time,
  re-projects locally with `steps.with_check_off()` (mirrors the backend's `Bake.project()`; keep them in step),
  and sends it through `checkoffs.py`: a Store-backed queue retried with backoff (15 s to 5 min, honoring
  `Retry-After`), re-applied to every poll until delivered, dropped when breadsheet refuses it, kept on
  `token_invalid`, and given up after two days.
- breadsheet-hass#13: `nudges.py` registers an HA webhook (id kept in the entry's data) and sends breadsheet its
  address with `PUT /api/v2/device/webhook`: a Cloud cloudhook if subscribed, else the external https URL on 443,
  else it clears the webhook and HA just polls. Re-registers when Cloud connects or the cloudhook changes, and
  after the next good poll if breadsheet was down. A nudge (body ignored) calls `async_request_refresh()`.
  Cloud can't be imported in tests (missing packages), so `cloud_component()` imports it only when it's loaded,
  and tests replace it. Never log the webhook address or id.
- breadsheet-hass#6: `blueprints/automation/breadsheet/bake_notifications.yaml` notifies on the step events
  (upcoming and late can each be turned off), one notification per step via `tag`, with a Done button whose action
  id is `BREADSHEET_DONE|<bake id>|<position>`; the blueprint's `mobile_app_notification_action` trigger calls
  `breadsheet.complete_step`. Mode `queued` with a 1.5 s pause spaces same-minute steps. Users import it from its
  GitHub URL (the Marketplace zip holds only `custom_components/breadsheet`); the README link comes with #8.
  `tests/test_blueprint.py` runs it as a real automation.
- breadsheet-hass#7: the blueprint's `live_activities` input (off by default) keeps one card per bake (tag
  `breadsheet_<bake id>`, `live_update: true`) counting down to the next step. It moves on with the integration's
  `breadsheet_bake_changed` event (fired from `step_events.py` when a bake appears or its next step, time, or
  progress changes, including web check-offs), clears before waits over 4 hours, comes back with the
  `breadsheet_step_upcoming` before the next step, skips steps due within a minute, and clears on
  `breadsheet_bake_ended`. Every update sends `silent` and `alert_once` (iOS ignores `silent` on a start). Not yet
  checked on a real phone: see the Android test plan on breadsheet-hass#7.
- breadsheet-hass#5: `panel.py` registers the "Breadsheet" sidebar panel (`/breadsheet`) while an account is
  loaded, serving `frontend/breadsheet-panel.js`. The script calls the websocket command `breadsheet/panel_url`,
  which gets `POST /api/v2/panel-tokens` from breadsheet and returns the website's `/panel` URL with a 12-hour
  token in its fragment, and shows it in a full-height frame; `/breadsheet/<bake id>` opens that bake. Tests
  need `home-assistant-frontend` (in `requirements_test.txt`).
- breadsheet-hass#8: `README.md` (install, pairing, the blueprint import link, Live Activities requirements,
  privacy) and `docs/releasing.md` (tagging, the release asset, the `hacs/default` submission). No release yet.
- Config entries are keyed on the breadsheet user id, which `POST /api/v2/devices` returns as `user`. The title
  is the username.
- Work starts in Second Rise milestone M3 (backlog items 21 through 28, plus agent tools
  in M4, items 33 and 35). The backend endpoints this integration needs ship in M2 and
  M3 of the `breadsheet` repo; don't build against endpoints that aren't in the spec.

## Working Rules
- Read this file and the spec before starting any task.
- Look up current versions (HA Core, its required Python version, test tooling,
  Marketplace requirements) rather than assuming them. HA's developer APIs change often.
- Show a plan before changing anything.
- Never commit or push without explicit instruction.
- Test against a non-production HA instance only, never the household's production HA.
- This repo is public. Keep personal infrastructure details (hostnames, IPs, tokens) out
  of code, docs, tests, and fixtures.

## Stack
| Layer | Technology |
|---|---|
| Language | Python (whatever version current HA Core requires) |
| Platform | Home Assistant custom integration (config entries, async) |
| HTTP | `aiohttp` via HA's shared client session |
| Tests | `pytest-homeassistant-custom-component` |
| Distribution | Home Assistant Marketplace, installed from tagged GitHub release assets |
| IDE | PyCharm Professional |

## Planned Repo Structure
Confirm against current HA and Marketplace docs during item 21.

```
breadsheet-hass/
├── custom_components/breadsheet/
│   ├── __init__.py        # Setup and unload of the config entry; stores runtime data
│   ├── manifest.json      # Domain, version, requirements, iot_class (cloud_polling)
│   ├── config_flow.py     # Pairing-code config flow
│   ├── api.py             # Thin async client for /api/v2
│   ├── coordinator.py     # Adaptive polling DataUpdateCoordinator
│   ├── calendar.py        # One calendar event per bake step
│   ├── sensor.py          # Per-bake current step, next step, next time, progress
│   ├── services.yaml      # breadsheet.complete_step
│   ├── panel.py           # Sidebar panel at /breadsheet with panel tokens
│   ├── llm_api.py         # M4: Breadsheet LLM API and its tools
│   ├── diagnostics.py     # Redacted diagnostics
│   ├── brand/             # icon.png and icon@2x.png (Marketplace and HA's brands proxy)
│   └── translations/en.json  # UI strings (custom integrations don't use strings.json)
├── blueprints/automation/breadsheet/
│   └── bake_notifications.yaml
├── tests/
└── docs/
```

## How the Integration Works
- **Pairing.** The config flow asks for a pairing code (`XXXX-XXXX`) from breadsheet.com
  and exchanges it at `POST /api/v2/devices` for a device token, stored in the config
  entry. The user's breadsheet key never enters HA.
- **Auth.** Every API call sends the device token as a bearer header. The token carries
  all four scopes (`recipes:read`, `recipes:write`, `bakes:read`, `bakes:write`).
- **Data flow.** The integration pulls everything from breadsheet. breadsheet's only
  connection to HA is an empty "poll now" webhook nudge after a Fancy bake changes
  (breadsheet-hass#13), and only when HA is reachable from the internet; otherwise polling
  covers it.
- **Polling.** `GET /api/v2/bakes?status=active&alert=fancy` every 60 seconds while a
  Fancy bake is active, every 2 minutes otherwise. Honor the server's suggested
  interval, its ETag, and any `Retry-After`. Idle polls must stay cheap: they're what
  keep the backend's database asleep.
- **Only Fancy bakes** reach HA. Simple bakes never appear.
- **Panel.** The sidebar panel at `/breadsheet` embeds breadsheet's bake view with a
  short-lived token from `POST /api/v2/panel-tokens` in the URL; no cookies.

## Resilience Rules
A running bake must survive a breadsheet outage of minutes to an hour.

- A failed poll must not make entities unavailable. HA's `DataUpdateCoordinator` does
  that by default, which would silence calendar triggers and notifications mid-bake.
  Keep serving the last successful data, expose a "last synced" attribute, and mark
  entities unavailable only once data is clearly stale (past the bake's projected end).
- `breadsheet.complete_step` records `completed_at` at the moment it's called, sends it
  to the API, and queues the call for retry with backoff if breadsheet is unreachable.
  Re-project the local schedule immediately so notifications stay correct.
- Check-off is idempotent on the server (first write wins), so retries are always safe.
- 503 responses carry the standard error body (`code: service_unavailable`) and a
  `Retry-After` header; back off accordingly.

## API Contract
- Use only the `/api/v2` endpoints the spec assigns to HA. The contract only grows, so
  never depend on a field or endpoint before the spec says it ships.
- Every error returns JSON with a stable `code` and a plain-language `message`. Branch
  on `code`, never on `message` text.
- Times arrive in UTC and in the bake's IANA time zone. Durations are in seconds.
- Missing detail means unknown, never zero: a null `active_seconds` is not 0.

## Blueprint
Inputs: notify targets, warn before each step (on), remind when late (on), Live Activities (off). It triggers on the integration's step events, so the lead time is the integration's
option, not a blueprint input.
- A notification before and at each step, with a Done action calling
  `breadsheet.complete_step`.
- One late reminder at +5 minutes, then nothing.
- Same-minute steps as separate notifications, 1 to 2 seconds apart.
- Live Activities (opt-in) via the companion app's `live_update` payload: `tag` is the
  bake id, one activity updated across consecutive steps, ended before waits over 4
  hours, no update for zero-wait steps. See the spec for every field.

## AI Agents (M4)
- Register a **Breadsheet** LLM API using HA's LLM API helpers, containing only
  breadsheet tools. HA's MCP Server integration exposes it to MCP clients; Assist
  conversation agents can use it directly.
- Tools are a thin layer over `/api/v2`: they hold no data and enforce nothing the API
  doesn't. The tool list and scopes are in the spec.
- Tool calls send `X-Breadsheet-Via: agent`, plus `X-Breadsheet-Agent: <name>` when HA's
  LLM context identifies the client. Non-agent calls send `X-Breadsheet-Via: ha`.
- Tool descriptions spell out units (seconds), time zone handling, the "unknown is not
  zero" rule, and that `cancel_bake` can't be undone.
- The README must tell users to connect assistants to the Breadsheet MCP URL only, and to
  review HA's "Expose all LLM APIs" option, which is on by default.

## Security
- Never log, print, or include the device token or panel tokens in diagnostics; redact
  them.
- Treat everything returned by the API (recipe names, step text) as untrusted text: it
  comes from other users via the community library.

## Release and Distribution
- Every version is a tagged GitHub release with a packaged asset; the Marketplace
  installs from release assets, not arbitrary scripts.
- Keep `manifest.json`'s version in sync with the release tag.
- New community repos are submitted through the `hacs/default` repository. Check the
  Marketplace contributor docs (contributors.home-assistant.io/marketplace) for current
  manifest requirements before the first release.

## Development
```bash
uv venv --python 3.14 .venv && uv pip install --python .venv/bin/python -r requirements_test.txt
.venv/bin/python -m pytest --cov=custom_components.breadsheet --cov-report=term-missing
```
- `pytest-homeassistant-custom-component` trails HA releases; check which `homeassistant` it pins.
- hassfest locally: `docker run --rm --platform linux/amd64 -v "$PWD":/github/workspace ghcr.io/home-assistant/hassfest`.
- **Throwaway HA** (never the household HA): a `ghcr.io/home-assistant/home-assistant` container named
  `breadsheet-hass-dev` on port 8123, config in the git-ignored `.ha-config/` (its generated login is in
  `.ha-config/dev-credentials.txt`), with `custom_components/breadsheet` mounted read-only and
  `BREADSHEET_API_URL=http://host.docker.internal:5050` pointing it at the local backend. Restart the container
  after code changes.
- `BREADSHEET_API_URL` is the only way to pair against a non-production backend. It's read when pairing and
  saved in the config entry.

## Coding Conventions
- Async only. Never block the event loop; no `requests`, no synchronous I/O.
- Use HA's shared `aiohttp` client session, not a new one.
- Config entries only; no YAML configuration for the integration.
- Follow HA's integration quality scale where practical, and use translations for every
  user-facing string.
- Include comments for non-obvious logic; preserve existing comments when refactoring
  unless the related code is removed.

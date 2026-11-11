# Breadsheet for Home Assistant

Phone alerts for your bakes from [breadsheet.com](https://www.breadsheet.com), the scheduler for long recipes
like sourdough. Start a recipe on breadsheet.com with **Fancy** alerts, and Home Assistant puts a card on your
phone's lock screen counting down to the next step (iOS and Android 16+), tells you when each step is due, and once
more if you're running late. Tap **Done** on the notification to check the step off.

<!-- ha-marketplace:exclude-start -->
[![Open your Home Assistant instance and open this repository in the Marketplace.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=brystmar&repository=breadsheet-hass&category=integration)
<!-- ha-marketplace:exclude-end -->

## What you get

- **Live Activities (iOS) and Live Updates (Android)**, on by default: one card per bake showing the next step, a
  countdown to it, and progress.
- **Notifications** when each step is due, and one reminder 5 minutes late, then nothing. A warning before each
  step (2 minutes ahead by default) is optional. Steps due in the same minute arrive separately, in order.
- **A Done button** that checks the step off on breadsheet. It works through breadsheet outages: the check-off
  is saved with the time you tapped and sent when breadsheet is back.
- **A Breadsheet sidebar panel** showing your bakes inside Home Assistant, where you can check off and skip steps.
  Tapping a notification or a lock-screen card opens the bake there.
- **Entities:** a calendar of your bake steps, and per bake a device with current step, next step, next step
  time, and progress sensors.
- **Events** for your own automations: `breadsheet_step_upcoming`, `breadsheet_step_due`,
  `breadsheet_step_late`, `breadsheet_bake_changed`, and `breadsheet_bake_ended`.

Only bakes started with **Fancy** alerts reach Home Assistant. Simple bakes (calendar file export) never do.

## Requirements

- Home Assistant **2026.11** or later (the built-in Marketplace).
- A breadsheet.com account.
- For phone notifications: the Home Assistant companion app on each phone.
- For live cards, see [Live Activities and Live Updates](#live-activities-and-live-updates).

## Install

1. In Home Assistant, go to **Settings → Marketplace**, search for **Breadsheet**, and install it. (The first
   time, Home Assistant warns that Marketplace content isn't tested or supported by Home Assistant; only admins
   can install.)
2. Restart Home Assistant.

## Pair with breadsheet

[![Open your Home Assistant instance and start setting up Breadsheet.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=breadsheet)

1. Sign in on breadsheet.com, open your **profile**, and in the **Home Assistant** section choose **Get a
   pairing code**. You get a code like `K7QX-2M4P`, good once, for ten minutes.
2. In Home Assistant, go to **Settings → Devices & services → Add integration → Breadsheet**, and enter the
   code.

Your breadsheet key never enters Home Assistant: pairing trades the code for a device token that works only for
recipes and bakes. To disconnect, remove the connection from your breadsheet profile; Home Assistant then asks
you to pair again.

Each breadsheet account can be paired once per Home Assistant. Every Home Assistant paired with an account gets
that account's Fancy bakes, so if you run two (say, a test instance), pair them with different accounts.

## Notifications

Import the blueprint, then create an automation from it:

[![Open your Home Assistant instance and show the blueprint import dialog with a specific blueprint pre-filled.](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fbrystmar%2Fbreadsheet-hass%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fbreadsheet%2Fbake_notifications.yaml)

- **Phones to Notify:** each phone's companion-app notify action, such as `notify.mobile_app_my_phone`
  (**Settings → Tools → Actions** lists them).
- **Warn Before Each Step?:** off by default.
- **Remind When Late?:** on by default.
- **Live Activities (iOS) and Live Updates (Android):** on by default; see below.

If you turn on warnings, how far ahead they come is the integration's option: **Settings → Devices & services → Breadsheet →
Configure** (2 minutes by default; 0 turns warnings off).

Notifications show step text from recipes, including community recipes other people wrote. Home Assistant only
displays it.

## Live Activities and Live Updates

The blueprint's **Live Activities (iOS) and Live Updates (Android)** input is on by default; turn it off if you only
want notifications. Each bake gets one lock-screen card with the next
step, a countdown, and progress. The card goes away during waits longer than 4 hours (Apple limits a Live
Activity to 8 hours) and comes back just before the next step. It clears when the bake ends.

Requirements, from the [companion app docs](https://companion.home-assistant.io/docs/notifications/live-activities):

- **iOS:** iOS 17.2 or later, Home Assistant 2026.7 or later, and Home Assistant reachable from your phone
  (for the token handshake).
- **Android:** Android 16 or later, Home Assistant 2026.7 or later. On Samsung phones, turn on **Live
  notifications for all apps** in developer options to get the status bar chip.

**Android testers wanted.** Live Updates haven't been checked on real Android phones yet, including what Android
15 and older show. If you try it, please [open an issue](https://github.com/brystmar/breadsheet-hass/issues)
with your phone, Android version, and what you saw.

## Sidebar panel

Once Breadsheet is set up, **Breadsheet** appears in Home Assistant's sidebar. It shows your active Fancy bakes,
the same bake view as breadsheet.com, where you can check off or skip steps and switch alerts. Tapping a
notification or a lock-screen card opens that bake here.

The panel signs in with a short-lived token Home Assistant gets from breadsheet each time it opens, so it works
inside the companion app without your breadsheet key. That token can only read and update bakes, and it lasts
12 hours.

## How it stays in sync

- Home Assistant asks breadsheet for your active Fancy bakes every minute while one is running and every
  2 minutes otherwise. Quiet checks are cheap for breadsheet.
- When Home Assistant is reachable from the internet (Home Assistant Cloud, or your own https address on port
  443), breadsheet also nudges it right after a change, so a bake you start on breadsheet.com shows up within
  seconds. The nudge carries no data: Home Assistant still fetches your bakes itself.
- If breadsheet is down, everything keeps running from the last copy: notifications, the calendar, sensors,
  and the cards. Done taps wait and are sent later with the time you tapped.

## Privacy

Home Assistant stores the device token from pairing, and the bakes it fetched so a restart during an outage
doesn't lose them. The sidebar panel's tokens are never stored. Diagnostics leave out the token, your breadsheet address, and recipe text. breadsheet stores
which Home Assistant connections you've paired and, if Home Assistant registered one, the webhook address it
nudges. See [breadsheet.com/privacy](https://www.breadsheet.com/privacy).

<!-- ha-marketplace:exclude-start -->
## Development

See [CLAUDE.md](CLAUDE.md) for the design, the test setup, and how to run a throwaway Home Assistant against a
local breadsheet backend. Releases are tagged `v<version>` with the same version in `manifest.json`; the release
workflow attaches `breadsheet.zip`, which the Marketplace installs.
<!-- ha-marketplace:exclude-end -->

## License

[MIT](LICENSE)

# Releasing and the Marketplace

How a version of breadsheet-hass gets to people. Thomas does the steps that publish anything (tags, releases,
the `hacs/default` pull request).

## Before a release
- `main` has everything for it, and CI is green: the Validate workflow runs the HACS action, hassfest, and the
  tests.
- `custom_components/breadsheet/manifest.json` has the new `version`. `hacs.json`'s `homeassistant` is the oldest
  Home Assistant this release supports (`2026.11.0`, the first with the Marketplace).
- The breadsheet backend this release calls is in production. Check `docs/api-v2.md` for every endpoint it uses.

## Release
1. On GitHub, draft a release with a new tag `v<version>` (for example `v0.1.0`) on `main`, and publish it. A tag
   alone isn't enough: the Marketplace reads the latest published release.
2. The Release workflow checks that the tag matches `manifest.json` and attaches `breadsheet.zip`, with the
   integration's files at the zip's root. Check that the asset is there.
3. Pre-releases only reach people who turn on the Marketplace's Pre-release switch.

## First listing: `hacs/default`
Until the repository is listed, people add it by link (Marketplace → Add repository by link). To list it:
1. Make sure both the HACS action and hassfest pass without ignored checks, the repository has a description and
   topics, issues are on, and a full release exists.
2. Fork `hacs/default` from your personal account (not an organization) and branch from `master`.
3. Add `brystmar/breadsheet-hass` to the `integration` file, in alphabetical order.
4. Open the pull request and fill in its template: the checklist, a link to the latest release, and links to a
   passing HACS action run and a passing hassfest run. Don't request reviewers.

The catalog refreshes every few hours; Home Assistant reads it every six.

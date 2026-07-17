# Desktop Release Runbook

How the **0xCopilot / Atlas** desktop app is built, signed, published, and
auto-updated. The pipeline is
[`.github/workflows/release-desktop.yml`](../../.github/workflows/release-desktop.yml).

- **Trigger:** pushing a `v*` git tag **publishes** a release. A manual
  `workflow_dispatch` run **only** produces downloadable artifacts (an unsigned
  dry-run — it never publishes).
- **Runners:** one native runner per (platform, arch) — `macos-14` (arm64),
  `macos-15-intel` (x64), `windows-latest` (x64). No cross-build: `stage.mjs`
  runs `pip install` on the target arch to populate each service's
  `site-packages` with matching wheels, so the self-contained runtime must be
  built on a host of that arch.
- **Signing degrades gracefully:** every signing step is guarded on the
  presence of its secret. With no cert configured the build still succeeds and
  produces **unsigned** artifacts (a loud `::warning::` is emitted). Add the
  secrets below to get signed + notarized output.

> **Coordination note.** This workflow drives `electron-builder` but does not
> own [`apps/desktop/electron-builder.yml`](../../apps/desktop/electron-builder.yml).
> For a publishable release the packaging config must additionally define:
> installer targets (`dmg` + `zip` for mac, `nsis` for win — the `zip`/`nsis`
> targets are what electron-updater consumes), `extraResources` staging
> `resources/runtime` into the app, a `publish` provider
> (`github`, owner `0x-copilot-dev`, repo `0x-copilot`), and `mac.notarize` set
> so notarization runs when the API-key credentials are present. Those live on
> the packaging track (`feat/desktop-packaging`).

---

## 1. Prerequisites — repository secrets

Set these as **repository (or environment) secrets** on
`0x-copilot-dev/0x-copilot`. Every one is optional: omit a group and that
platform is built unsigned. Names below are the **exact** identifiers the
workflow reads.

| Secret                 | Platform | Purpose                                       | Format                                                         |
| ---------------------- | -------- | --------------------------------------------- | -------------------------------------------------------------- |
| `MAC_CSC_LINK`         | macOS    | Developer ID Application signing certificate  | Base64 of the exported `.p12` (`base64 -i DeveloperID.p12`)    |
| `MAC_CSC_KEY_PASSWORD` | macOS    | Password for the `.p12` above                 | Plaintext                                                      |
| `APPLE_API_KEY`        | macOS    | App Store Connect API key for **notarytool**  | Base64 of the `AuthKey_XXXXXXXXXX.p8` (`base64 -i AuthKey.p8`) |
| `APPLE_API_KEY_ID`     | macOS    | Key ID of the API key                         | e.g. `XXXXXXXXXX`                                              |
| `APPLE_API_ISSUER`     | macOS    | Issuer ID (UUID) of the App Store Connect key | e.g. `69a6de7f-...`                                            |
| `WIN_CSC_LINK`         | Windows  | Authenticode code-signing certificate         | Base64 of the `.pfx`                                           |
| `WIN_CSC_KEY_PASSWORD` | Windows  | Password for the `.pfx` above                 | Plaintext                                                      |
| `GITHUB_TOKEN`         | all      | Publish the GitHub release + upload assets    | Provided automatically by Actions (job has `contents: write`)  |

**How the workflow uses each:**

- **macOS signing** — `MAC_CSC_LINK` / `MAC_CSC_KEY_PASSWORD` are exported to
  electron-builder as `CSC_LINK` / `CSC_KEY_PASSWORD` (electron-builder accepts
  a base64 `CSC_LINK` directly — no file needed). When `MAC_CSC_LINK` is
  absent, `CSC_IDENTITY_AUTO_DISCOVERY=false` is set so the build skips signing
  cleanly instead of failing on a keychain lookup.
- **macOS notarization** — `APPLE_API_KEY` is base64-decoded to a `.p8` file
  under `$RUNNER_TEMP`; its path plus `APPLE_API_KEY_ID` and `APPLE_API_ISSUER`
  feed electron-builder's notarytool integration. Absent ⇒ notarization is
  skipped.
- **Windows signing** — `WIN_CSC_LINK` / `WIN_CSC_KEY_PASSWORD` are exported as
  `CSC_LINK` / `CSC_KEY_PASSWORD` on the Windows leg. Absent ⇒ unsigned build
  (SmartScreen will warn end users until a cert is in place).

### Alternative: Windows Azure Trusted Signing

Instead of a `.pfx`, Windows can sign via **Azure Trusted Signing** (cloud
HSM, no long-lived cert to store). That path needs the electron-builder config
`win.azureSignOptions` (endpoint + account + certificate-profile) and these
env/secret values injected on the Windows leg:
`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`. If you adopt it,
add those as secrets, wire them into the Windows job env, and remove/ignore
`WIN_CSC_LINK`. This runbook's default path is the `.pfx` above.

### Dependabot coverage

No dependabot change is needed. `.github/dependabot.yml`'s `github-actions`
ecosystem is registered at `directory: /`, which globs **all** files under
`.github/workflows/` — so the SHA-pinned actions in `release-desktop.yml` are
already tracked for weekly updates.

---

## 2. Cut a release

The git tag **triggers** the run, but the release version comes from
`apps/desktop/package.json`'s `version`. **They must match** — electron-builder
names the GitHub release `v${version}` and stamps that version into
`latest*.yml`. A tag that disagrees with `package.json` publishes to the wrong
release and breaks the update feed.

1. **Bump the version.** Edit `apps/desktop/package.json` `"version"` to
   `X.Y.Z`, commit, and merge to `main`.
2. **Verify signing secrets** are present for the platforms you intend to ship
   signed (Settings → Secrets and variables → Actions).
3. **Tag and push:**
   ```bash
   git tag vX.Y.Z            # X.Y.Z == apps/desktop/package.json version
   git push origin vX.Y.Z
   ```
4. **Watch the run** under Actions → `release-desktop`. Each matrix leg:
   `npm ci` → build the desktop bundle → `stage.mjs` the runtime → sign (if
   secrets present) → `electron-builder --publish always`.
5. **Publish the draft.** electron-builder creates a **draft** GitHub release
   on `0x-copilot-dev/0x-copilot` and uploads every leg's assets to it:
   - macOS: `*.dmg` (human install), `*.zip` + `latest-mac.yml` (updater feed),
     `*.blockmap` (differential download).
   - Windows: `*.exe` (NSIS installer), `latest.yml` (updater feed), `*.blockmap`.

   Review the draft, confirm all three legs uploaded, then click **Publish
   release**. Auto-update clients only see the release once it is published (not
   draft).

Every run — tag or dispatch — also uploads a `desktop-<platform>-<arch>`
workflow artifact (14-day retention) mirroring the distributables.

---

## 3. Unsigned dry-run first (recommended)

Before spending a real tag, validate the whole build on a branch:

1. Actions → `release-desktop` → **Run workflow** → pick the branch → Run.
2. Because it is a `workflow_dispatch` (not a tag push), electron-builder runs
   with `--publish never`. Nothing reaches the GitHub release.
3. Download the `desktop-<platform>-<arch>` artifacts and smoke-test the app
   (unsigned — macOS Gatekeeper will require right-click → Open, Windows
   SmartScreen will warn; that's expected for a dry-run).

Use this to shake out staging/packaging regressions without minting a tag.

---

## 4. Auto-update contract (electron-updater)

The published release **is** the update channel. There is no separate update
server.

- **Feeds.** electron-builder writes `latest-mac.yml` (mac) and `latest.yml`
  (win) next to the installers. Each records the current version, the artifact
  filename, its sha512, and blockmap. The desktop app's electron-updater client
  (provider `github`, `0x-copilot-dev/0x-copilot`) fetches the matching feed
  from the **latest published** release.
- **Version compare.** electron-updater compares the feed's `version` against
  the running app's `package.json` version (semver). A higher version triggers
  a background download of the referenced artifact — the **`zip`** on macOS
  (Squirrel.Mac) and the **NSIS `.exe`** on Windows — using the blockmap for a
  differential (delta) download.
- **Install on quit.** After `update-downloaded`, the client applies the update
  when the user **quits** the app (electron-updater's `autoInstallOnAppQuit`,
  NSIS `installOnQuit`) unless it calls `quitAndInstall()` sooner. The next
  launch is the new version. Users are never interrupted mid-session.
- **Signing is required for macOS auto-update.** Squirrel.Mac refuses to swap
  in an update whose signature doesn't validate, so the `zip` must be signed
  (and notarized to clear Gatekeeper on first launch). Windows NSIS updates
  apply unsigned, but ship signed to avoid SmartScreen friction.

> The electron-updater **client** wiring in `apps/desktop/main` (adding the
> `electron-updater` dependency, `checkForUpdatesAndNotify`, and the
> update/restart UI) is part of the Phase-8 packaging integration. This runbook
> covers the release/publish side and the contract that client must honor.

---

## 5. Rollback

- **Before publishing (draft):** just delete the draft release. Nothing was
  ever visible to clients.
- **After publishing:**
  1. Delete the GitHub release (Releases → the release → Delete), which removes
     the published `latest*.yml` feeds so clients stop rolling forward to the
     pulled build.
  2. Delete the tag:
     ```bash
     git push origin :refs/tags/vX.Y.Z   # delete remote tag
     git tag -d vX.Y.Z                    # delete local tag
     ```
  3. If a previous good release still exists, its feeds become "latest" again.
- **Clients that already downloaded** the bad update cannot be un-notified.
  Real remediation is to **roll forward**: bump to `X.Y.(Z+1)`, fix, and cut a
  new release. Semver always wins, so a higher patch supersedes the pulled one.

---

## 6. Quick reference

| Action                     | How                                                                     |
| -------------------------- | ----------------------------------------------------------------------- |
| Dry-run (unsigned, no pub) | Actions → `release-desktop` → Run workflow (any branch)                 |
| Publish a release          | Bump `apps/desktop/package.json` version, then `git push origin vX.Y.Z` |
| Where it publishes         | Draft GitHub release on `0x-copilot-dev/0x-copilot`                     |
| Update feeds               | `latest-mac.yml` / `latest.yml` attached to the published release       |
| Rollback                   | Delete release + tag; then roll forward with a higher patch             |

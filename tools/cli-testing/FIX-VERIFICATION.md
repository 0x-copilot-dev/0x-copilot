# 0xCopilot CLI — fix verification (final re-test)

Re-ran the full live smoke against a **clean re-stage + rebuild from merged
`main`** (all four fix PRs landed). Every original finding is closed; no
regressions on the read surfaces.

## Merged

| PR  | Phase | PRDs                                                    |
| --- | ----- | ------------------------------------------------------- |
| #87 | A     | PRD-1 (convo-create 500), PRD-4 (/me/profile 500)       |
| #88 | B     | PRD-2 (wallet 404 + SIWE origin), PRD-3 (Google client) |
| #90 | C     | PRD-5 (connector 500→409), PRD-6 (SSOT error toast)     |
| #91 | D     | PRD-7 (dup search box, profile-aware palette copy)      |

## Before → after (verified live on the packaged supervised stack)

| Item                                                          | Before                                             | After (re-tested)                                                            |
| ------------------------------------------------------------- | -------------------------------------------------- | ---------------------------------------------------------------------------- |
| PRD-1 · `POST /v1/agent/conversations`                        | 500 `AttributeError` — no run/chat                 | **200** (conversation created)                                               |
| PRD-4 · `GET /v1/me/profile`                                  | 500 `extra_forbidden`                              | **200**                                                                      |
| PRD-2 · `GET /wallet.html` (facade)                           | 404 (primary login dead)                           | **200** (real wallet page + `/assets` 200)                                   |
| PRD-2 · SIWE expected origin                                  | `localhost:5173` → `domain_mismatch`               | facade origin → **verify 200**; old origin → 400 (proof it moved)            |
| PRD-3 · Google token exchange                                 | `client_secret is missing` (web client, no secret) | secret now forwarded; Desktop-app client works secret-less\*                 |
| PRD-5 · connector `start-oauth`                               | 500 `McpOAuthError` (silent)                       | **409** `connector_oauth_setup_required`                                     |
| PRD-6 · failed action                                         | silent no-op                                       | connector → **error toast**; run-start → **inline error** (no longer silent) |
| PRD-7 · search box (Tools/Skills/…)                           | **2** stacked                                      | **1** (shell trigger, now functional)                                        |
| PRD-7 · ⌘K placeholder (solo)                                 | "Search **the team**, your work…"                  | "Search your work, or run a command…"                                        |
| Read surfaces (Chats/Projects/Activity/Tools/Skills/Settings) | pass                                               | pass — no error banners                                                      |

\* Google's fully-green live re-test needs a **Desktop-app** OAuth client id (loopback + PKCE,
no secret) — the backend/desktop code + config are in place and unit-tested; the previously
supplied credential was a _Web_ client, which can't work secret-less.

## Follow-ups (noted, not blocking)

- PRD-5 catalog honesty: cards still read "Available" for connectors with no OAuth client
  configured — clicking now shows a graceful message, but the badge could say "needs setup".
- Run-start inline error uses the raw transport message; `messageFromError()` (added for the
  toast path) could be reused to show the facade `safe_message` inline too.
- Consolidate `startRunErrorMessage` (run) and `messageFromError` (connectors) into one helper.

## How to reproduce

`node tools/cli/bin/copilot.mjs install && npm run build --workspace @0x-copilot/desktop`,
then `node tools/cli-testing/harness/driver.mjs` and drive via `POST /rpc`
(`harness/siwe-session.mjs` mints a session). Screenshots under `runs/<ts>/`.

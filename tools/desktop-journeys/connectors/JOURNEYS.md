# Connector journeys

The connectors program (#385…#395) shipped almost entirely on unit tests. These
drive the **real packaged app** to check the parts a unit test structurally
cannot: that the desktop renderer actually reaches the main-brokered connect
IPC, and that the suggestion appetite and its mute list survive a real
round-trip through `/v1/me/preferences` rather than living in component state.

```bash
COPILOT_JOURNEY_DOTENV=/path/to/services/ai-backend/.env \
APP_DIR="$PWD/apps/desktop" \
COPILOT_HOME="$PWD/apps/desktop/resources" \
python3 tools/desktop-journeys/connectors/connector_lifecycle.py
```

## What is deliberately NOT automated

**Completing a real vendor OAuth.** That means driving a third-party consent
screen with real credentials, which an automated journey should not do. CN-06
stops at the last boundary the app itself owns — "the renderer asked main to
start the flow" — which is exactly the wiring that was missing before #395.
Everything past that point (the loopback bind, the system browser, the code
exchange) is covered by `apps/desktop/main/connectors/oauth-coordinator.test.ts`.

## Matrix

| ID    | Entry state              | Actions                                        | Expected outcome                                                                            | Automation               |
| ----- | ------------------------ | ---------------------------------------------- | ------------------------------------------------------------------------------------------- | ------------------------ |
| CN-01 | Fresh install, past FTUE | Settings → Model & behavior                    | The appetite control shows `unblock_only` — the SERVER's default, not a client guess.       | `connector_lifecycle.py` |
| CN-02 | CN-01                    | Change the appetite to "always"                | `GET /v1/me/preferences` reports `mode: always`. A control that only moved on screen fails. | `connector_lifecycle.py` |
| CN-03 | Appetite set to "always" | Persist a per-slug mute                        | The mute lands AND the appetite survives it — two surfaces write this block, neither wins.  | `connector_lifecycle.py` |
| CN-04 | A muted connector exists | Re-enter Settings → Model & behavior           | The connector is listed under "Muted". Without this the mute is a one-way door.             | `connector_lifecycle.py` |
| CN-05 | CN-04                    | Unmute                                         | The override round-trips to `true` server-side.                                             | `connector_lifecycle.py` |
| CN-06 | Any                      | Invoke `connector.connect` for an unknown slug | Main HANDLES it (fails at start-oauth). "channel not in allowlist" is the regression.       | `connector_lifecycle.py` |
| CN-07 | A live `mcp_auth` gate   | Press Connect on the consent card              | The system browser opens at the vendor. **Needs a live gate** — not automated.              | manual                   |
| CN-08 | Connected, then restart  | Reopen the app                                 | The connector is still connected (token survived in the vault).                             | manual                   |

## Known limitation found while writing these

`useConnectorSuggestions` fetches preferences once on mount and never refetches.
A mute made **while Settings is already open** therefore will not appear until
Settings is re-entered — CN-04 had to leave the surface and come back before the
list rendered. In the shipping flow this is unreachable (a mute is made from a
suggestion card in the Run cockpit, so Settings mounts fresh afterwards), which
is why it is recorded here rather than fixed.

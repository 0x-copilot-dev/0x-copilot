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
| CN-09 | Linear connected by hand | Ask for one Linear WRITE, then DECLINE it      | The run parks, `gate.opened` lands, the decline lands `gate.resolved: cancelled`, no write. | `gate_audit_events.py`   |

## Known limitation found while writing these

`useConnectorSuggestions` fetches preferences once on mount and never refetches.
A mute made **while Settings is already open** therefore will not appear until
Settings is re-entered — CN-04 had to leave the surface and come back before the
list rendered. In the shipping flow this is unreachable (a mute is made from a
suggestion card in the Run cockpit, so Settings mounts fresh afterwards), which
is why it is recorded here rather than fixed.

## CN-09 — the write gate must leave an audit trail (`gate_audit_events.py`)

`gate.opened` / `gate.resolved` for the **write-approval** gate shipped in
commit 875db5a7 and have **never been observed on a real run**. Before it, the
emitter admitted only `mcp_auth_required`, so a write parked on the LangGraph
interrupt, was decided by a human, and then executed or refused **leaving no
ledger row at all**: the run could not say that a gate had ever been in the way,
or who let the write through. Recording the decision but not the gate that
forced it is the first thing a regulated buyer asks about — and a unit test
cannot show that the emitters fire on the path a real Linear write actually
travels. This journey drives that path.

### What it asserts

1. a Linear WRITE **parks** the run (`status: waiting_for_approval`);
2. the run ledger contains `gate.opened`, its `gate_id` equal to the parked
   `approval_id`, carrying `auth_state: insufficient` — the member that means
   _the standing authorization is not sufficient for THIS operation_, and the
   machine-readable discriminator between the write gate and the OAuth-connect
   gate (`missing` / `expired`);
3. the human **declines**;
4. the ledger then contains `gate.resolved` for the **same** `gate_id` with
   `outcome: cancelled`;
5. nothing was written to Linear.

`gate.opened` present without a paired `gate.resolved` is a failure, not a
detail: `PendingWorkProjector` calls a gate pending until it is resolved, so an
unpaired open is a gate that stays open forever.

### The safety contract — it declines, and there is no approve path

The write is aimed at the user's **real** Linear workspace, so the journey is
built around one rule: **park, then DECLINE.** Four independent layers, in the
order they take effect:

1. **It refuses to ask for a write that could auto-execute.** Before sending
   anything it reads the app's own authenticated surfaces and exits `2` if a
   write would bypass the gate: a per-connector `write_policy: allow_always`, a
   tool-use policy whose `write` / `destructive` axis reads `auto`, or a
   composer pill on **Bypass** (`Posture.BYPASS` lifts every ASK/REQUIRE gate to
   ALLOW). Anything it cannot read counts as a risk — "we could not establish
   that a write would park" is not the same sentence as "a write would park".
2. **The write it asks for is inert even if it dispatched.** The target is the
   placeholder issue id `00000000-0000-4000-8000-000000000000`, and the prompt
   forbids searching for, guessing or substituting a real issue, and forbids
   creating one. The intent is unambiguously a write — which is what makes the
   PDP gate it — but there is no reachable object to mutate.
3. **It only ever declines.** There is no approve path in the file. A declined
   gate never dispatches: `ToolAccessGate` returns `approved=False`, the
   middleware returns the typed `permission_denied` refusal, and the connector
   call is never made.
4. **It sweeps.** Anything still pending at teardown is declined too, so the
   reused profile is never left holding a card a later human could approve.

The "nothing landed" assertion is made from the run's own evidence — the typed
declined-write refusal is present, `gate.resolved.outcome` is `cancelled`, there
is no `write.applied` / `effect.applied`, and no tool result names the gated op
without reading as a refusal. It does **not** read Linear's audit log: the
guarantee is structural (the gate refuses before dispatch), and the journey says
so rather than implying a read-back it never performed.

### Prerequisites — one of them is a human

- **Linear must already be connected, out of band.** `driver.mjs` replaces
  `shell.openExternal` and only records the URL, so no journey can finish a
  vendor OAuth (README, "A journey can NEVER complete an OAuth connect").
- **It reuses FS-F's profile on purpose.** `DriverSession` derives its userData
  subdir from its `name` (`journey-<name>-reuse` when `fresh=False`), so sharing
  the name `fs-f-linear-mcp` is the only way to inherit the one connect a human
  already made — this journey adds no new manual setup. Its evidence file
  (`cn-09-gate-audit-evidence.json`) and `cn09-*` screenshots are named
  distinctly so the two journeys never overwrite each other inside
  `runs/fs-f-linear-mcp/`.
- **Re-stage after any `services/*` change.** `<COPILOT_HOME>/runtime/**` is a
  snapshot. `gate.opened` for the write gate is younger than most stages on a
  dev machine, so a stale stage reports exactly the absence this journey looks
  for — with total confidence.

Step 1, by hand, once — skip it if FS-F already connected in this profile:

```bash
COPILOT_RUNTIME_DIR="$PWD/apps/desktop/resources" \
COPILOT_DESKTOP_USER_DATA_SUBDIR=journey-fs-f-linear-mcp-reuse \
  npm run dev --workspace @0x-copilot/desktop
# "Tools" in the left rail → Linear → Connect → finish OAuth in the browser
# → the row must read Connected → QUIT the app.
```

Step 2, the journey:

```bash
COPILOT_HOME="$PWD/apps/desktop/resources" \
COPILOT_JOURNEY_DOTENV=/path/to/services/ai-backend/.env \
  python3 tools/desktop-journeys/connectors/gate_audit_events.py
```

`COPILOT_RUNTIME_DIR` in step 1 and `COPILOT_HOME` in step 2 must be the same
directory — the connection lives in the supervised Postgres under that staged
runtime, so a different stage is a different database. Do not add
`COPILOT_DEV=1` / `COPILOT_AUTH_MODE=dev-mint`: that signs in a different
persona, whose connectors this journey cannot see.

### Reading the outcome

| Exit | Outcome   | What it means here                                                                                                                                               |
| ---- | --------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `0`  | `passed`  | The pair fired on a real run: opened, declined, resolved `cancelled`, nothing written.                                                                           |
| `1`  | `FAILED`  | A write parked and the ledger is silent (the 875db5a7 regression), the pair is unmatched, the outcome is wrong, or a declined write looks like it dispatched.    |
| `2`  | `blocked` | Linear is not connected, a write here could auto-execute, the model never attempted the write, or the decline could not be delivered. No write was ever at risk. |
| `3`  | `skipped` | No staged runtime, or no BYOK key in `services/ai-backend/.env`.                                                                                                 |

A `blocked` run is a legitimate report, not a pass and not a failure: the
capability or the precondition is absent, so the assertion was never exercised.
The one thing it never does is substitute a run that looks like a gated write
and isn't.

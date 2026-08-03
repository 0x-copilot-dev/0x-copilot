# Desktop journey run — 2026-08-03

Verification notes from the Postgres-removal branch. **Read the caveat first:
the pass/fail counts from that session are not a verdict on the product.**

## The measurement was flawed — twice

A first pass ran all 53 journeys back-to-back and reported 17 passing. Two
defects in the _runner_ inflated the failure count, and a third contaminated it:

1. **Wrong interpreter.** `python3` resolved to a miniconda 3.10 on the host,
   but the journeys need 3.11+ (`StrEnum`). Seven `generative-workflows`
   journeys died at import before executing a line of product code.
2. **BLOCKED scored as FAIL.** Several journeys deliberately stop and
   self-report `{"outcome": "blocked", "reason": ...}` when a documented
   prerequisite is missing (a local stdio fixture, an OAuth connect, the
   env-gated deterministic model). They exit non-zero, so a naive runner counts
   them as defects.
3. **Contention and stray processes.** 53 supervised stacks (Electron +
   embedded Postgres + three services) run back-to-back leave orphans that the
   next journey inherits.

A corrected partial re-run (3.13, blocked classified separately) **overturned
the earlier failures**:

| Journey                               | First pass  | Corrected                                                     |
| ------------------------------------- | ----------- | ------------------------------------------------------------- |
| `chat-nav-model/ftue_first_message`   | FAIL 130.8s | **PASS 10.4s**                                                |
| `chat-rich-cards/budget_overrun`      | FAIL 122.5s | **PASS 61.8s**                                                |
| `filesystem-access/attach_folder_row` | FAIL 120.5s | **PASS 10.2s**                                                |
| `connectors/gate_audit_events`        | FAIL        | **BLOCKED** (correct)                                         |
| `agent-todos/todo_panel`              | PASS        | PASS standalone (0.0s in-suite = stray-process contamination) |

That re-run covered 12 of 52 before being stopped. **The remaining ~40 journeys
have no trustworthy result.** Nothing here should be cited as "N/53 passing".

## How to run these properly

```bash
/opt/homebrew/bin/python3.13 tools/desktop-journeys/<set>/<journey>.py
```

Re-stage first if anything under `services/*` changed — the staged runtime is a
snapshot, not a link:

```bash
node tools/desktop-runtime/stage.mjs --platform darwin --arch arm64
```

For a full sweep: pin one provider (drop the other keys from
`services/ai-backend/.env`), allow a cooldown between journeys, and reap stray
`Electron` / `driver.mjs` / `uvicorn` processes between runs.

## Root causes worth keeping

Established by reading logs and product source, independent of the flawed counts.

| Journey                                                           | Class                        | Root cause                                                                                                                                                                                                                                                                                                                                                                                                             |
| ----------------------------------------------------------------- | ---------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `chat-nav-model/focus_only`                                       | STALE — **removed**          | Asserted Focus-only behind `STUDIO_ENABLED=false`. That flag no longer exists in `useRunMode.ts`; dev `9993aaf0` made Studio the default. Its own JOURNEYS.md predicted the inversion.                                                                                                                                                                                                                                 |
| `connectors/connector_lifecycle`                                  | STALE — **removed**          | Probed IPC channel `connector.connect`. `main/connectors/channels.ts` documents that verb as deliberately removed and folded into `connector.authorize`.                                                                                                                                                                                                                                                               |
| `generative-workflows/g0_plain_chat`                              | STALE                        | "event type is outside the grammar: `quality.control_bound.v1`" — a newer event the journey's grammar does not list.                                                                                                                                                                                                                                                                                                   |
| `chat-rich-cards/tool_budget_setting`                             | MODEL-SENSITIVE              | The "errored" cards are `ToolBudgetRejected`: the per-run cap (2/2) was enforced **correctly**, backend logged 4 `tool_call_completed` / 2 `final_response` / zero error events. Haiku overshot a budget the journey's original model respected.                                                                                                                                                                       |
| `chat-rich-cards/rich_chat`                                       | MODEL-SENSITIVE              | The fleet child did render nested tool activity; the assertion wants a specific activity string. Model-driven tool choice differs per model.                                                                                                                                                                                                                                                                           |
| `filesystem-access/jD_bypass`, `jH_bypass_demo`                   | **NEEDS A PRODUCT DECISION** | A fresh install reports `filesystem_bypass_enabled: true`, so the execution-mode pill is enabled and Bypass is selectable with the master switch off. `filesystem_bypass.py:177` sets `DEFAULT_FILESYSTEM_BYPASS_OFFERED = True`, while the same module's docstring (line 24) states the master switch is "default **off**". The journeys encode the docstring. Resolve the contradiction before changing either side. |
| `g3`, `g10`                                                       | BLOCKED (self-declared)      | "installed Desktop does not currently propagate the env-gated deterministic model to supervised ai-backend".                                                                                                                                                                                                                                                                                                           |
| `g4`                                                              | BLOCKED (self-declared)      | "binary DOCX publication/preview is not yet a model-visible artifact contract".                                                                                                                                                                                                                                                                                                                                        |
| `g5`–`g9`                                                         | BLOCKED (self-declared)      | "the public facade cannot yet register/execute the checked-in local stdio fixture in a fresh installed Desktop profile".                                                                                                                                                                                                                                                                                               |
| `connectors/gate_audit_events`, `filesystem-access/jF_linear_mcp` | ENVIRONMENT                  | Need a connected Linear MCP; the endpoint answers 401 (`Bearer realm="OAuth"`).                                                                                                                                                                                                                                                                                                                                        |
| `attached_folder_stops_asking`, `jB_attached_folder_is_silent`    | ENVIRONMENT                  | Need macOS native automation to drive the OS folder picker.                                                                                                                                                                                                                                                                                                                                                            |

## What this run did verify

Driven live against the packaged app, re-staged from this branch:

- The ai-backend runs the file-native store with no relational database: only
  `atlas_backend` exists in `pgdata/base`, and `atlas_ai` is gone.
- Real runs complete end-to-end — `model_delta` / `final_response` /
  `run_completed`, with runs, messages and events persisted as JSONL.
- The Anthropic `temperature` 400 is fixed: the same journey that produced two
  `run_failed` events now completes with real model output.

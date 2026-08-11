# generative-workflows — release journeys for agent-authored work

This file is the **requirement spec** for the Studio workflow release gate: what
G0–G10 must prove, and where each one executes today. It is not a script
inventory — most of these journeys no longer live in this directory.

> **Read this first.** Commit `e8622a1d` ("64 scripts become 9, and a phase
> runner keeps the verdicts") folded G0 and G3–G10 out of this folder into the
> consolidated journeys. The **IDs survived as phase names**, so a G-number
> still names exactly one gate — but you run it through its host journey, not a
> `gN_*.py` script. Only G1 and G2 are still standalone here.

## Where each journey runs

| ID      | Runs as                                                 | Host journey                                              |
| ------- | ------------------------------------------------------- | --------------------------------------------------------- |
| **G0**  | phase `AS-1` — "plain chat publishes no rich UI at all" | [artifacts_and_surfaces.py](../artifacts_and_surfaces.py) |
| **G1**  | standalone script                                       | [g1_markdown_lifecycle.py](./g1_markdown_lifecycle.py)    |
| **G2**  | standalone script                                       | [g2_csv_lifecycle.py](./g2_csv_lifecycle.py)              |
| **G3**  | phase `IP-2`                                            | [installed_payload.py](../installed_payload.py)           |
| **G4**  | phase `IP-3`                                            | [installed_payload.py](../installed_payload.py)           |
| **G5**  | phase `IP-4`                                            | [installed_payload.py](../installed_payload.py)           |
| **G6**  | phase `IP-5`                                            | [installed_payload.py](../installed_payload.py)           |
| **G7**  | phase `IP-6`                                            | [installed_payload.py](../installed_payload.py)           |
| **G8**  | phase `IP-7`                                            | [installed_payload.py](../installed_payload.py)           |
| **G9**  | phase `IP-8`                                            | [installed_payload.py](../installed_payload.py)           |
| **G10** | phase `IP-9`                                            | [installed_payload.py](../installed_payload.py)           |

G1 and G2 remain unfolded because the work is unfinished, not because they are
special — see [MIGRATION.md](../MIGRATION.md). Their _helpers_ were already
lifted into [\_workspace_lib.py](../_workspace_lib.py) and
[artifacts_and_surfaces.py](../artifacts_and_surfaces.py); only their
end-to-end narratives still stand alone.

Because `installed_payload.py` runs its phases over a shared boot, a G-phase can
fail for a reason a **previous** phase caused. Read the phase table the run
prints; the exit code is only its aggregate.

## The two passes

Every case is proven twice, and the difference matters:

1. **Deterministic local-workspace pass.** The real desktop app and its three
   supervised services run against local fixture connectors only. A test MCP
   fixture exposes the email, timeline, Discord, and filesystem data in
   [`scenarios/local-communications.json`](./scenarios/local-communications.json).
   It accepts a staged commit only into its isolated fixture store. This pass
   proves grant, read, edit, diff, approval, retry, and receipt behaviour
   without sending mail, publishing a post, or contacting a real community.
2. **Credentialed live-reasoning pass.** The same local fixture store, but the
   journey signs in and pastes an OpenAI or Anthropic BYOK value from
   `services/ai-backend/.env` through the real FTUE/Settings password field.
   This proves a real model chooses tools, creates artifact surfaces, and stages
   effects through the shipping product.

The second pass is deliberately **not** a mock. It is a real model request in the
real desktop process; only the destinations of potentially destructive tools are
local, deterministic fixture services.

## Preconditions and safety

- Build and stage the current desktop and runtime before execution:
  `make desktop-supervised` (re-stage after any `services/*` change — the staged
  runtime is a snapshot, not a link).
- Run with Python **3.13**. A bare `python3` may resolve to a 3.10 that dies at
  import on `StrEnum`, which reads as a product failure and is not one.
- Each script gets a fresh `COPILOT_DESKTOP_USER_DATA_SUBDIR`. It never points
  at a user workspace, mailbox, social account, or Discord guild.
- Provider keys load only via `load_env_key("openai" | "anthropic")` and fill
  through `ftue_add_key`. Scripts must never interpolate a key into an
  assertion, exception, screenshot label, log, or fixture.
- The local connector fixture has an explicit `fixture://` target identity, a
  write audit log, and a reset endpoint. A journey fails if a write carries an
  `https://`, SMTP, Discord, X, or other non-fixture destination.
- A keyless pass always runs before the credentialed pass for workflows that
  must be honest about unavailable model/tool access.

## Required journey matrix

| ID  | Story and action                                                                                                                                          | Must prove in deterministic pass                                                                                                                                | Additional live-reasoning proof                                                                                | Visual parity state                                   |
| --- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| G0  | **Plain chat, no surface.** Ask a factual/coding question that needs neither a tool nor an artifact.                                                      | One assistant response; no tool card, artifact tab, staged-write bar, or receipt launcher.                                                                      | Model answers without inventing a tool/action.                                                                 | Focus answer / Studio answer                          |
| G1  | **Markdown lifecycle.** Create `project-brief.md`, load it, edit the heading and one paragraph, then stage the saved revision.                            | Local file grant appears once; file surface renders raw/editor states; diff is revision-pinned; reject/restore/approve preserve exact bytes and provenance.     | Model uses the local file tool and produces the requested Markdown without calling unrelated tools.            | Markdown editor, diff, held approval                  |
| G2  | **CSV row-set.** Create `pipeline.csv`, inspect it, change two rows, hold one high-risk row, and apply only approved rows.                                | Table surface displays columns/preview; row-level diff, agent hold, partial result, retry, and receipt counts are exact.                                        | Model selects the CSV tool and respects the explicit held row.                                                 | Dataset table, row diff, partial apply                |
| G3  | **Code artifact.** Create `src/normalize.ts`, ask for a small patch and test/read it before staging.                                                      | Code renderer, syntax/raw fallback, unified diff, provenance, exact content hash, and no inline execution bypass.                                               | Model authors code, reads it back, and stages—not executes—any proposed mutation.                              | Code editor, patch diff, held approval                |
| G4  | **DOCX.** Generate `status-update.docx`, preview/export it, change the title, and stage a replacement.                                                    | Binary/document preview, download/export, immutable version switch, and safe fallback all work; export does not mutate the source.                              | Model writes the document content and publishes it as an artifact.                                             | Document preview, version/diff state                  |
| G5  | **Local email triage.** Read the fixture inbox, summarize the `Q3 renewal` thread, draft a reply, edit it, then approve the local send.                   | Inbox read card has trusted provenance; draft is visibly staged; approval commits only to `fixture://mail`; audit row captures recipient/thread/revision.       | Model finds the right thread and uses the draft/reply operation rather than claiming it sent an email in chat. | Mail record, compose/diff, approval/receipt           |
| G6  | **Local X timeline.** Read the fixture timeline, draft a response to `@northstar`, revise its tone, and approve publication to the fixture timeline.      | Timeline/user/post context is represented; staged post diff has exact target/account; reject leaves fixture timeline unchanged.                                 | Model chooses a reply draft and follows target/account constraints.                                            | Timeline record, post diff, approval                  |
| G7  | **Local Discord moderation.** Read `#launch-room`, summarize the decision, draft a pinned announcement mentioning `@maya` and `@leo`, then approve it.    | Guild/channel/thread identity survives provenance; mention list and channel target are diffed; a failed first commit retries idempotently.                      | Model reads the relevant messages and stages exactly one announcement.                                         | Conversation record, announcement diff, retry receipt |
| G8  | **Mixed work.** Read the local email plus Discord context, update `launch-plan.md`, generate `launch-metrics.csv`, and stage the two independent effects. | Multiple tabs/surfaces do not bleed state; cross-run Sources/Approvals/Agents rail routes to the right surface; approval order does not change final artifacts. | Model produces a bounded operation tree: reads first, two staged writes, no surprise external actions.         | Multi-surface Studio, Sources/Approvals rails         |
| G9  | **Recovery and honesty.** Expire a workspace grant, make one local tool return `unknown operation`, reject an approval, and cancel a streaming run.       | Gate card parks/resumes the same call; unknown tool is honest; retry cannot reuse stale approval/revision; raw fallback keeps data visible.                     | Model observes errors and asks/recovers instead of fabricating success.                                        | Gate, error/raw fallback, rejected diff               |
| G10 | **Retention and reopen.** Complete G1/G2, close/reopen the conversation, then review receipt/sources/pending work.                                        | Event replay reconstructs editor/table/receipt without stale payloads; no pending card remains after terminal decisions.                                        | Model usage attribution has run/conversation/purpose rows, with no key or prompt secret exposure.              | Reopened Studio, receipt, Sources/Approvals           |

## Capability flags are attestations, not bypasses

Several G-phases stay fail-closed until the shipping product supplies a
capability. Setting a flag only allows the phase to **attempt** the
authenticated registration or artifact path; every UI, event-ledger, target,
digest, audit, and postcondition assertion still fails closed.

| Flag                                   | Gates                                                                                       |
| -------------------------------------- | ------------------------------------------------------------------------------------------- |
| `GENUI_DETERMINISTIC_SUPERVISED_READY` | the deterministic lane for G3/G10 — the installed supervisor propagating the fake model     |
| `GENUI_BINARY_ARTIFACTS_READY`         | G4 — binary DOCX publication, streaming, preview/export, immutable revision, replacement    |
| `GENUI_LOCAL_FIXTURE_BRIDGE`           | G5–G9 — a fresh authenticated profile owning the checked-in stdio fixture for the whole run |

A phase whose flag is absent exits with `"outcome": "blocked"` and reports that
reason. **Blocked is not failed and not passed** — a runner that scores it as
either is reporting a defect that does not exist, or hiding one that does.

## Local communications fixture

`scenarios/local-communications.json` defines stable users, mailbox threads,
timeline posts, Discord guild/channel messages, and the expected writes. It is
deliberately realistic enough to require entity resolution, but contains no real
people, accounts, domains, or credentials.

The local connector implements these operations over its own throwaway store:

| Domain   | Read operations                 | Staged write operations                      |
| -------- | ------------------------------- | -------------------------------------------- |
| Mail     | `list_threads`, `get_thread`    | `draft_reply`, `send_draft`                  |
| Timeline | `list_posts`, `get_post`        | `draft_reply_post`, `publish_draft`          |
| Discord  | `list_channels`, `get_messages` | `draft_announcement`, `publish_announcement` |
| Files    | `list`, `read`, `stat`          | `write_revision`, `apply_rowset`             |

All write operations return a `fixture://` receipt and reject any target outside
the scenario namespace. The runner resets fixture state before each case and
asserts exact postconditions afterward.

## Release gates represented by this set

- **Studio default-on / Focus opt-in:** G0–G10 begin in Studio and explicitly
  verify the Focus alternative where a rich surface exists.
- **No custom UI for ordinary chat:** G0 is the counterexample protecting the
  "surfaces, not transcripts" rule from becoming "surface everything."
- **Safe local effects:** G1–G8 prove every mutation is stage → exact diff →
  user decision → connector/local-file effect, never a chat-side assertion.
- **Read/write boundaries:** G5–G9 cover auto-run reads, granted/expired access,
  held writes, unknown tools, cancellation, and retries.
- **Durability/accountability:** G10 proves replay, receipts, provenance,
  pending-work disappearance, and attributed usage.

## Non-goals

- No real email delivery, social publication, Discord message, filesystem
  mutation outside the throwaway fixture root, or browser account login.
- No key material in source, fixture JSON, screenshots, assertions, failure
  output, or generated parity HTML.
- No "blocked but green" placeholder for a required rich surface. Missing UI,
  wrong target, an unauthorized write, or a fabricated completion is a failed
  release gate.

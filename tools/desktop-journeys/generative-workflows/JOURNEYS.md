# generative-workflows — release journeys for agent-authored work

This suite is the release gate for the Studio workflow rather than a component
test collection. Each case drives the packaged, supervised Electron desktop app
with Playwright, asserts the user-visible result and the authenticated facade
truth, and writes screenshots plus service logs under `runs/generative-workflows/`.

It deliberately proves two different things in two passes:

1. **Deterministic local-workspace pass.** The real desktop app and its three
   supervised services run against local fixture connectors only. A test MCP
   fixture exposes the email, timeline, Discord, and filesystem data in
   [`scenarios/local-communications.json`](./scenarios/local-communications.json).
   It accepts a staged commit only into its isolated fixture store. This pass
   can prove grant, read, edit, diff, approval, retry, and receipt behavior
   without sending mail, publishing a post, or contacting a real community.
2. **Credentialed live-reasoning pass.** The exact same local fixture store is
   used, but the journey signs in and pastes an OpenAI or Anthropic BYOK value
   from `services/ai-backend/.env` via the real FTUE/Settings password field.
   The key is never printed, logged, committed, or included in a prompt. This
   proves that a real model chooses tools, creates artifact surfaces, and
   stages effects through the shipping product. External side effects remain
   impossible because every writable connector is local.

The second pass is intentionally **not** a mock. It is a real model request in
the real desktop process; only the destinations of potentially destructive
tools are local, deterministic fixture services.

## Preconditions and safety

- Build and stage the current desktop and runtime before execution:
  `make desktop-supervised` (re-stage after any backend change).
- Each script gets a fresh `COPILOT_DESKTOP_USER_DATA_SUBDIR`. It never points
  at a user workspace, mailbox, social account, or Discord guild.
- Provider keys are loaded only with `load_env_key("openai" | "anthropic")`
  and filled through `ftue_add_key`; scripts must never interpolate a key into
  an assertion, exception, screenshot label, log, or fixture.
- The local connector fixture has an explicit `fixture://` target identity,
  write audit log, and reset endpoint. A journey fails if a write carries an
  `https://`, SMTP, Discord, X, or other non-fixture destination.
- A keyless pass is always run before the credentialed pass for workflows that
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

## Local communications fixture

`scenarios/local-communications.json` defines stable users, mailbox threads,
timeline posts, Discord guild/channel messages, and the expected writes. It is
deliberately realistic enough to require entity resolution, but contains no
real people, accounts, domains, or credentials.

The future local connector must implement these operations over its own
throwaway store:

| Domain   | Read operations                 | Staged write operations                      |
| -------- | ------------------------------- | -------------------------------------------- |
| Mail     | `list_threads`, `get_thread`    | `draft_reply`, `send_draft`                  |
| Timeline | `list_posts`, `get_post`        | `draft_reply_post`, `publish_draft`          |
| Discord  | `list_channels`, `get_messages` | `draft_announcement`, `publish_announcement` |
| Files    | `list`, `read`, `stat`          | `write_revision`, `apply_rowset`             |

All write operations return a `fixture://` receipt and reject any target that
is not in the scenario namespace. The runner resets fixture state before each
case and asserts exact postconditions afterward.

## Two-step execution protocol

For every G1–G10 case, the future `*.py` journey executes:

1. **Keyless / deterministic:** sign in locally, seed the local fixture,
   perform the required user interactions, and assert the surfaces, diffs,
   staging ledger, service-side effect audit, screenshots, and fixture state.
   If a model is needed to reach an operation, this phase uses a deterministic
   test route; it never calls a provider.
2. **BYOK / live:** start a fresh app, sign in, use `ftue_add_key` with the
   selected provider, repeat the natural-language prompt against the same
   local fixture, then assert the same durable state. Tool selection may vary
   only where the scenario explicitly permits it; absent/mis-targeted writes,
   missing surfaces, or unapproved execution fail the journey.

The script saves a screenshot after: sign-in/FTUE, first tool result, artifact
or record surface, edit/diff, approval decision, terminal receipt, and each
failure/recovery state. It also records authenticated facade responses for
run events and final fixture audit rows through `DriverSession.transport()`.

## Release gates represented by this set

- **Studio default-on / Focus opt-in:** G0–G10 begin in Studio and explicitly
  verify the Focus alternative where a rich surface exists.
- **No custom UI for ordinary chat:** G0 is the counterexample protecting the
  “surfaces, not transcripts” rule from becoming “surface everything.”
- **Safe local effects:** G1–G8 prove every mutation is stage → exact diff →
  user decision → connector/local-file effect, never a chat-side assertion.
- **Read/write boundaries:** G5–G9 cover auto-run reads, granted/expired access,
  held writes, unknown tools, cancellation, and retries.
- **Durability/accountability:** G10 proves replay, receipts, provenance,
  pending-work disappearance, and attributed usage.

## Implementation order

1. Add `local-fixture-connector/` with a process-local MCP fixture server,
   reset/audit API, and the JSON scenario loader. It must be usable by the
   supervised desktop app without a network account.
2. Add shared `DriverSession` helpers for fixture seeding, authenticated
   postcondition reads, tool-card/surface/diff/approval selectors, and safe
   BYOK setup. Secrets remain opaque end-to-end.
3. Implement G0, G1, G2, G5, and G9 first: together they cover the primary
   product claims and the critical safety/error paths.
4. Add G3/G4/G6/G7/G8/G10 plus the parity surfaces for editor, diff, table,
   record, gate, receipt, and rail states.
5. Only after all deterministic cases are green, run the credentialed live
   matrix with OpenAI and Anthropic separately; save screenshots/logs as CI or
   release artifacts, never in git.

## Non-goals

- No real email delivery, social publication, Discord message, filesystem
  mutation outside the throwaway fixture root, or browser account login.
- No key material in source, fixture JSON, screenshots, assertions, failure
  output, or generated parity HTML.
- No “blocked but green” placeholder for a required rich surface. Missing UI,
  wrong target, an unauthorized write, or a fabricated completion is a failed
  release gate.

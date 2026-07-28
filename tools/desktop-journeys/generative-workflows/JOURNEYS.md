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

## G0 — executable supervised plain-chat release evidence

[`g0_plain_chat.py`](./g0_plain_chat.py) is the executable, credentialed G0
release journey. It launches a fresh production-posture Electron session through
`DriverSession`; the session has a unique
`COPILOT_DESKTOP_USER_DATA_SUBDIR`, an embedded host-executable staged runtime,
and **no** `COPILOT_FACADE_URL`. A green run therefore uses the app's packaged
supervisor rather than a mock or separately started facade.

The script chooses a real local OpenAI or Anthropic BYOK value only through
`load_env_key`, fills the actual FTUE password field, and checks the authenticated
`GET /v1/agent/models` facade response for `configured: true`. It never logs,
serializes, names a screenshot after, or otherwise exposes the value.

| G0 evidence                         | Exact assertion                                                                                                                                                                                                                                                                                                                                                                             |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `01-g0-sign-in.png`                 | Fresh local sign-in gate completed through `sign-in-button`.                                                                                                                                                                                                                                                                                                                                |
| `02-g0-byok-composer.png`           | Real FTUE key form reached `first-run-composer` after the provider password field accepted the local BYOK value.                                                                                                                                                                                                                                                                            |
| `03-g0-plain-answer-no-rich-ui.png` | Exactly one nonempty `[data-testid^=tc-chat-message-][data-role=assistant]` is visible.                                                                                                                                                                                                                                                                                                     |
| UI leak guard                       | No `tc-chat-tool-*`, `tc-chat-fleet-*`, `tc-tabs`, `artifact-frame`, staged-write card/control, `receipt-v2-launch`, or canonical `receipt-v2-surface` test ID is present.                                                                                                                                                                                                                  |
| Authenticated facade truth          | The bound `#/convo/<id>` has exactly one run; `GET /runs/{id}` and replay both report `completed`; replay contains exactly one `final_response`, no legacy tool activity/event, no v2 tool-execution event (`action.classified`, `read.executed`, `gate.opened`, `gate.resolved`), no artifact/effect or non-receipt surface; `/surfaces` contains at most the terminal audit-only receipt. |

The only successful exit that is not a pass is a visibly reported `SKIP G0:` for
a documented local prerequisite: no built desktop bundle, no host-executable
staged runtime, or no OpenAI/Anthropic BYOK value in the ignored local `.env`.
Boot, sign-in, BYOK, model catalog, run, UI, or facade assertion failures are
hard failures. Screenshots and driver logs stay under the git-ignored
`tools/desktop-journeys/runs/generative-workflows-g0-plain-chat/` directory.

## G3–G10 executable status

The G3–G10 scripts are executable release stories backed by
[`_g3_g10_support.py`](./_g3_g10_support.py). They all require the installed
payload, production supervisor posture, a unique Desktop user-data subdirectory,
authenticated facade reads, deterministic screenshot names, and a fresh
throwaway root for authored workspace files. Preflight exits `2` with
`"outcome":"blocked"` when a declared local capability is absent; it never turns
a missing surface, fixture bridge, or provider into a pass.

| Journey | Script and exact executable assertions                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | Current real-run status                                                                                                                                                                                                                                                                          |
| ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| G3      | [`g3_code_artifact.py`](./g3_code_artifact.py) reads `src/normalize.ts` and its test before publication; verifies exact TypeScript bytes/SHA-256, inert code renderer with no executable descendant or raw fallback, unified held diff, immutable proposal material, facade surface/provenance, native digest-pinned approval, exact final file bytes, and absence of shell/sandbox/test execution. Screenshots are `g3-<mode>-workspace-grant`, `-code-renderer-provenance`, `-unified-held-diff`, and `-approved-receipt`.                                                                         | Live lane can run with staged host runtime, macOS native-dialog automation, and local BYOK. Deterministic lane blocks until the installed supervisor propagates the fake-model environment (`GENUI_DETERMINISTIC_SUPERVISED_READY=stdio-v1`).                                                    |
| G4      | [`g4_docx_artifact.py`](./g4_docx_artifact.py) requires valid ZIP/WordprocessingML bytes, document preview plus safe fallback, export byte equality without source revision mutation, immutable title revision switch, binary replacement diff metadata, native approval, and exact final DOCX bytes. Screenshots cover preview, export/fallback, version switch/diff, and receipt.                                                                                                                                                                                                                  | Live blocks until binary DOCX publication, streaming, preview/export, and fallback are model-visible (`GENUI_BINARY_ARTIFACTS_READY=docx-v1`); deterministic also lacks a create/revise/stage phase switch. Current inline-text publication and Markdown/plain-text rendering cannot satisfy G4. |
| G5      | [`g5_local_email_triage.py`](./g5_local_email_triage.py) resets the fixture, lists/reads only `thr_q3_renewal`, proves trusted `fixture://mail` provenance, edits to a higher draft revision, approves one exact recipient/thread/revision, and reconciles the facade receipt with the hash-chained fixture audit. Screenshots cover the mail record, edited draft, and local-send receipt.                                                                                                                                                                                                          | Both lanes block until the authenticated public MCP registry can own the checked-in stdio fixture for the lifetime of the fresh profile (`GENUI_LOCAL_FIXTURE_BRIDGE=stdio-v1`). No real address can pass target assertions.                                                                     |
| G6      | [`g6_local_x_timeline.py`](./g6_local_x_timeline.py) reads the Northstar fixture post/account, proves the exact reply target, revises tone, rejects once with an unchanged timeline audit, then restores/approves exactly one fixture publication and receipt. Screenshots cover timeline context, rejected diff, and approved fixture post.                                                                                                                                                                                                                                                         | Same local stdio fixture prerequisite as G5; no X URL/account or non-`fixture://` receipt is accepted.                                                                                                                                                                                           |
| G7      | [`g7_local_discord_moderation.py`](./g7_local_discord_moderation.py) preserves Launch Week guild/channel identity and exactly `@maya`/`@leo`, holds one pinned announcement, surfaces the injected first retryable failure, retries the same stage, and proves one failure plus one idempotent publish in fixture audit. Screenshots cover context/diff, retryable failure, and final retry receipt.                                                                                                                                                                                                 | Same local stdio fixture prerequisite as G5; no Discord endpoint can pass target assertions.                                                                                                                                                                                                     |
| G8      | [`g8_mixed_work.py`](./g8_mixed_work.py) reads fixture mail and Discord before exactly two independent held writes, proves two tabs plus Sources/Approvals/Agents routing, approves in reverse order, reads both exact outputs back, and reconciles two ordered fixture writes with the receipt. Screenshots cover multi-surface rails and the two-effect receipt.                                                                                                                                                                                                                                   | Same local stdio fixture prerequisite as G5, including staged-write projection from the fixture's prepare/commit contract.                                                                                                                                                                       |
| G9      | [`g9_recovery_honesty.py`](./g9_recovery_honesty.py) proves the expired-grant gate resumes one operation ID, the declared unknown operation remains visible in raw fallback without a success claim, revision 1 receives HTTP 409 after revision 2 exists, the current revision is rejected without apply, a streaming run terminates cancelled without a final answer, and fixture audit records exactly one grant fault/unknown operation and no write. Screenshots cover gate, resumed call, raw fallback, stale/rejected diff, and cancellation.                                                 | Same local stdio fixture prerequisite as G5, with bridge support for the scenario-declared unknown-operation fault and a cancellable streaming checkpoint. Missing recovery projection is a hard failure.                                                                                        |
| G10     | [`g10_retention_reopen.py`](./g10_retention_reopen.py) publishes exact Markdown/CSV artifacts, applies two separately approved native workspace stages, closes and reopens the same profile/conversation, deep-links event replay to reconstruct document/table tabs, receipt, Sources, and Approvals, proves both pending-work endpoints contain no terminal stage, reads effective retention, and requires live run/conversation/purpose usage rows without BYOK or prompt-marker/content leakage. Screenshots cover creation, each held stage, reopened Sources, and terminal no-pending receipt. | Live lane can run with the same installed-runtime/macOS/BYOK prerequisites as G3. Deterministic lane blocks on installed-supervisor fake-model propagation.                                                                                                                                      |

The capability flags above are attestations, not bypasses: setting one only
allows the script to attempt the authenticated registration or artifact path.
Every UI, event-ledger, target, digest, audit, and postcondition assertion still
fails closed.

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

The G3–G10 scripts encode this protocol directly. G1/G2 keep their existing
release entry points until the shared driver is consolidated:

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

## Remaining harness prerequisites

The executable stories are now checked in. They stay fail-closed until the
shipping product/harness supplies these capabilities:

1. propagate the deterministic model configuration through the installed
   supervised Desktop runtime;
2. let a fresh authenticated Desktop profile own the checked-in stdio fixture
   connector for the full journey, including reset/audit and staged-write
   projection;
3. expose binary DOCX publication, streaming, preview/export, immutable
   revision, and replacement staging;
4. expose the G9 fixture faults and cancellable streaming checkpoint;
5. run the deterministic matrix first, then OpenAI and Anthropic live passes;
   retain screenshots/logs as release artifacts, never in git.

## Non-goals

- No real email delivery, social publication, Discord message, filesystem
  mutation outside the throwaway fixture root, or browser account login.
- No key material in source, fixture JSON, screenshots, assertions, failure
  output, or generated parity HTML.
- No “blocked but green” placeholder for a required rich surface. Missing UI,
  wrong target, an unauthorized write, or a fabricated completion is a failed
  release gate.

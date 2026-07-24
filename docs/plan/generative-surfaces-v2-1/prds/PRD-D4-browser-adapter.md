# PRD-D4 — Browser read, download, upload, and submit adapter 🎨

**Goal.** Converge the desktop agentic browser on the Operation Gateway. Navigation,
snapshot, wait, screenshot, and close remain bounded read/internal operations. Downloads
become artifacts. Uploads use authorized artifact revisions. Any click/form/submit that
can change external state is staged over an exact origin/action/payload fingerprint and
applied only by a browser executor.

## Implementer brief

Read:

1. `../01-sdr.md` principles and sequence S9.
2. `PRD-A3-operation-gateway.md`,
   `PRD-A5-commit-coordinator.md`, and
   `PRD-B2-artifact-renderers-editors.md`.
3. `services/ai-backend/src/agent_runtime/capabilities/browser/desktop_browser_provider.py`.
4. Browser constants and MCP registry integration.
5. Electron-main browser broker/worker implementation and its read-only tool schemas.
6. Existing tool policy, citations, offload, and audit paths.

The existing browser provider is intentionally read-only. Do not expose a generic
side-effecting click before the staged browser protocol is complete.

## Context

Browser automation has ambiguous semantics: navigation is generally a read, but a click
can like, purchase, delete, submit, or download. Tool names alone are insufficient.
Downloads are internal artifacts until the user/agent later saves them externally.
Form submissions require exact review but many websites provide weak idempotency and
reconciliation, so uncertain outcomes must remain honest.

## Interfaces consumed

- A3 descriptors/gateway/gates.
- A2 artifact repository.
- A4/A5 staging/commit.
- B2 file renderer/download.
- B3 canvas/Focus lifecycle.
- Existing desktop browser broker and MCP-compatible read provider.

## Interfaces exposed

- `BrowserOperationAdapter`.
- `BrowserEffectExecutor`.
- `BrowserActionPlan` and `BrowserPrecondition`.
- browser download/import artifact flow.
- shared browser action review surface/card.

## Design

### D1. Closed action classes

Read/internal:

- navigate;
- snapshot;
- wait;
- screenshot;
- close;
- scroll/focus with no external mutation.

Artifact:

- download response/file;
- screenshot explicitly published;
- page export explicitly published.

Potential external effect:

- click unknown or descriptor-marked action;
- form submit;
- upload plus submit;
- account/settings mutation;
- transaction/purchase;
- message/comment/post;
- delete/cancel/approve.

Unknown action defaults held. A model cannot label a click read-only.

### D2. Browser session authority

Electron main/browser worker owns:

- browser profile/session/cookies;
- origin policy and user consent;
- page handles;
- native downloads/uploads;
- action execution.

AI-backend receives opaque session/page/element refs and safe snapshots, never cookies
or credential stores. Facade does not expose broker routes/tokens.

### D3. Read operation adapter

Current read-only MCP façade may remain a transport adapter, but operations enter the
gateway before dispatch. Envelopes are run/session/nonce/expiry bound.

Read results:

- bounded accessibility/DOM snapshot;
- screenshot/result refs;
- citations/source origin;
- no automatic canvas unless explicitly useful.

Snapshot redacts password values, tokens, payment details, and fields marked sensitive.

### D4. Download as artifact

Browser worker:

1. validates response/download policy, filename, media type, size;
2. streams bytes to Artifact Service through a private bounded bridge;
3. verifies digest/size;
4. creates file/dataset/document artifact with browser provenance;
5. never saves autonomously to an arbitrary host path.

Web/desktop user download is a separate explicit user-gesture action. Saving the
artifact into `/workspace/` uses C3.

### D5. Upload source

Upload accepts an authorized `artifact_id@revision` or bounded user-selected file ref.
It never accepts server/local absolute paths. The stage preview shows filename,
media type, size, digest, destination origin/form context.

Selecting bytes for an upload may be internal; the external effect occurs when the
browser action transmits/submits them and is staged accordingly.

### D6. Action plan and precondition

```text
BrowserActionPlan
  session_ref, page_ref
  origin, top_level_origin
  action_kind
  element_ref, element_fingerprint
  form/action_url/method?
  canonical_fields_ref, fields_digest?
  upload_artifact_refs[]
  user_visible_summary

BrowserPrecondition
  page_generation
  origin
  element_fingerprint
  form_fingerprint?
```

Canonical fields exclude secrets from public summaries but include protected exact
values/ref digest in proposal. Approval binds origin, action, fields/uploads, and
precondition.

Cross-origin frame actions are explicit and more restrictive.

### D7. Stage and UI

Before side-effecting dispatch:

- create `browser_submission` proposal;
- show origin/site, action, target, redacted field summary, uploaded artifacts, and
  destructive/financial risk;
- never show password/secret plaintext;
- approve exact revision/digest;
- sensitive financial/destructive actions may require native/high-friction confirmation
  or remain unsupported.

Focus gets compact held-action card; Studio gets detailed review.

### D8. Browser executor

`prepare`:

- verifies live session/page/origin;
- re-resolves element/form by stable fingerprint;
- compares page generation/precondition;
- confirms uploads/artifact digests;
- performs no submit/click.

`apply`:

- executes exact action once via Electron main;
- records navigation/response/action receipt;
- never substitutes a “similar” element after drift.

`reconcile`:

- provider/site-specific when a stable transaction/result is observable;
- otherwise returns indeterminate after uncertain dispatch;
- never repeats a POST/purchase/message blindly.

### D9. Authentication and user gestures

Browser login/session consent is a gate, not effect approval. User gesture requirements:

- system file picker/download destination remains host/browser controlled;
- payment/passkey/2FA/native prompts remain user-driven;
- automation cannot synthesize protected browser confirmation where policy forbids it.

### D10. Security

- origin allow/deny policy;
- URL normalization and scheme allowlist;
- no `file:`, `javascript:`, privileged extension/internal pages;
- top-level and frame origin displayed;
- prompt injection in page content is untrusted and cannot change descriptors/policy;
- secrets redacted from snapshots/logs/events;
- bounded DOM/screenshot/download;
- session refs scoped to user/device/run and expire/revoke.

## Implementation plan

1. Inventory/read descriptors and gateway-wrap current read-only tools.
2. Add browser session/action contracts and private broker protocol.
3. Implement download-to-artifact.
4. Implement artifact-backed upload source.
5. Add action-plan proposal builder/stage surface.
6. Implement browser executor prepare/apply/reconcile.
7. Add native/user-gesture gates for protected actions.
8. Enable side effects by explicit action cohort; keep generic click read-only/held until
   classified.
9. Add security/adversarial/live browser suite.

## Test plan

### Reads/downloads

- navigation/snapshot executes once, no unwanted surface;
- hostile page cannot inject policy/tool metadata;
- download exact digest/filename sanitization/limits;
- no autonomous host-path save.

### Side effects

- unknown click stages, zero broker action before approval;
- form fields/upload digests exact;
- page/element/origin drift → zero action;
- duplicate command → one action;
- uncertain POST result → indeterminate/no retry;
- auth/login gate independent of approval.

### Security

- cross-origin iframe, redirects, privileged schemes;
- password/token/payment redaction;
- forged/expired session/element refs;
- oversize DOM/screenshot/download;
- renderer never receives cookies/broker token.

### UI/live

- action review and destructive treatment;
- download artifact renderer;
- Focus/Studio behavior;
- live safe-site submit, drift, download, reconnect/reconcile smoke.

## Definition of done

- [ ] Existing browser reads enter gateway.
- [ ] Downloads become exact internal artifacts.
- [ ] Upload sources are authorized artifact/user refs.
- [ ] Every external browser action stages before execution.
- [ ] Browser executor binds exact origin/action/payload/precondition.
- [ ] Uncertain actions are never blind-retried.
- [ ] Secrets/session authority remain Electron-main-only.
- [ ] UI, effect-path, and standard DoD pass.

## Out of scope

- Unsupported financial/legal high-risk automation.
- Generic arbitrary-JavaScript page execution.
- Silent host download destination.
- Cloud browser multi-tenant rollout without a separate threat review.

## Guardrails

- A click is not inherently a read.
- No generic side-effect tool before exact staging exists.
- No cookies/passwords/secrets in events.
- No similar-element substitution after drift.
- No retry of uncertain external submissions.

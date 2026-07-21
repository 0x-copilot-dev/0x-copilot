# PRD — P6b: Google Sheets read/write MCP connector

**Status:** Design / implementation-ready · **Phase:** P6 (Safe + Sheets connectors) — Sheets half · **Branch:** `claude/0xcopilot-first-run-onboarding-d7eb30`

This PRD is grounded in the shipped desktop-connector stack. Read alongside `docs/plan/first-run-onboarding/README.md` §7.3 and the AC9 connector code cited throughout.

---

## 1. Goal + scope

**Goal.** Ship a real Google Sheets MCP connector that can **read and write workbooks/cells**, appears as a 1-click connector in the FTUE tools popover (State B) and the Settings connectors destination, and gates writes behind explicit per-call user approval. It closes the gap the Drive connector documents: `desktop_profiles.yaml:118-120` says Drive "include[s] readable Sheets and Slides text, **not cell/formula … editing**" and "**No … chart operations**."

**In scope**

- A `gsheets` marketing catalog entry (`connectors/catalog.yaml`).
- A `google-sheets` desktop profile (`connectors/desktop_profiles.yaml`) with read + write scopes, a read + write tool contract, and per-call write approval metadata.
- The one net-new code change needed to make a **write** product scope requestable through the desktop OAuth start flow (today capped at `read`/`draft`).
- Parity/wiring notes for how the row renders in the FTUE tools popover (P4-owned surface) and the Settings connectors destination, and how per-chat scoping + per-call write approval already flow.

**Out of scope (explicit)**

- The FTUE tools-popover _component_ itself (P4 net-new; this PRD only guarantees the catalog row + write-scope path it consumes).
- Safe{Wallet} (P6a).
- Building a bespoke Sheets MCP _server process_. The design pins an HTTPS MCP endpoint + pre-registered OAuth client as an **operator setup** input; whether that endpoint is an official Google Sheets MCP or a self-hosted server is a deployment decision (see Open questions). The code path is identical either way.
- The hosted-trial lane (shelved).

---

## 2. Files to CREATE and EDIT

### Backend (`services/backend`)

| Action | Path                                               | Purpose                                                                                                                                                                                                                                                                                          |
| ------ | -------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| EDIT   | `src/backend_app/connectors/catalog.yaml`          | Add `gsheets` marketing entry (slug + display_name "Google Sheets" + description + `icon_hint: gsheets`) so the profile has a non-orphan card to reconcile against (`profile_catalog.py:225-228`).                                                                                               |
| EDIT   | `src/backend_app/connectors/desktop_profiles.yaml` | Add `google-sheets` profile: Google Workspace group, `spreadsheets.readonly` (read) + `spreadsheets` (write) scopes, read + write tools, per-call approval on writes, `requires_pre_registered_client: true`, `release_stage: preview` + `requires_preview_gate: true`, loopback+deep-link PKCE. |
| EDIT   | `src/backend_app/connectors/oauth_coordinator.py`  | Extend `start()` param + `_requested_permissions()` to accept/expand `"write"` (today `Literal["read","draft"]` and `wanted={"read"}(+draft)`; `write` never requested — see §4).                                                                                                                |
| EDIT   | `src/backend_app/connectors/desktop_routes.py`     | Widen `DesktopStartOAuthRequestModel.requested_product_scope` to `Literal["read","draft","write"]` (currently `:91`).                                                                                                                                                                            |

### Contracts (`packages/api-types`)

| Action | Path                        | Purpose                                                                                                                                                                                                                  |
| ------ | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| EDIT   | `src/connectors-desktop.ts` | Widen `DesktopRequestedProductScope` to `"read" \| "draft" \| "write"` and update the "Write scopes are never requested from the desktop start body" comment (`:60-77`) to reflect the deliberate write reauthorization. |

### Tests

| Action | Path                                                              | Purpose                                                                                                                                                                                                                            |
| ------ | ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| EDIT   | `services/backend/tests/unit/connectors/test_desktop_profiles.py` | Sheets profile reconciles; read+write scopes present; write tools require per-call approval; not an orphan (extends existing `test_shipped_catalog_loads_and_reconciles` `:43`, `test_no_orphan_cards_in_shipped_catalog` `:147`). |
| EDIT   | `services/backend/tests/unit/connectors/test_desktop_oauth.py`    | `requested_product_scope="write"` returns the write scope in `requested_permissions`; write connect fails closed as `connector_oauth_setup_required` without an operator client.                                                   |
| EDIT   | `services/backend/tests/unit/connectors/test_desktop_routes.py`   | Start-OAuth route accepts `write` and rejects unknown scopes.                                                                                                                                                                      |
| EDIT   | `packages/api-types/src/connectors-desktop.test.ts`               | Type-level: `"write"` is assignable to `DesktopRequestedProductScope`.                                                                                                                                                             |

**No migration.** No new DB schema — the connector is a config-overlay row; installation reuses `mcp.servers` via the existing `_ensure_server` (`oauth_coordinator.py:264-296`).

---

## 3. New/changed signatures

### 3.1 `catalog.yaml` (data — insert after the `gdrive` entry, `catalog.yaml:44-47`)

```yaml
- slug: gsheets
  display_name: Google Sheets
  description: Read and write spreadsheet cells and formulas.
  icon_hint: gsheets
```

### 3.2 `desktop_profiles.yaml` (data — new profile after `google-drive`, `desktop_profiles.yaml:71-120`)

```yaml
- profile_id: google-sheets
  connector_slug: gsheets
  server_id: "desktop:google:sheets" # profile-owned seed; must not collide (profile_catalog.py:258)
  display_group: Google Workspace
  endpoint_template: "https://sheetsmcp.googleapis.com/mcp/v1" # OPERATOR-CONFIRMED endpoint (see §8)
  transport: http
  release_stage: preview
  requires_preview_gate: true
  verified_at: "2026-07-21"
  requires_pre_registered_client: true # forces the operator OAuth-client setup step
  callback_modes: [loopback_pkce, deep_link_pkce]
  reference_urls:
    - "https://developers.google.com/workspace/sheets/api/reference/rest"
  permissions:
    - identifier: "https://www.googleapis.com/auth/spreadsheets.readonly"
      kind: oauth_scope
      required_for: read
      admin_consent_required: false
    - identifier: "https://www.googleapis.com/auth/spreadsheets"
      kind: oauth_scope
      required_for: write
      admin_consent_required: false
  tools:
    # reads — approval: session
    - {
        tool_name: get_spreadsheet,
        product_scope: read,
        risk: low,
        approval: session,
      }
    - {
        tool_name: get_values,
        product_scope: read,
        risk: low,
        approval: session,
      }
    - {
        tool_name: batch_get_values,
        product_scope: read,
        risk: low,
        approval: session,
      }
    - {
        tool_name: search_spreadsheets,
        product_scope: read,
        risk: low,
        approval: session,
      }
    # writes — approval: per_call, names carry write-terms so the runtime classifier flags them (see §4)
    - {
        tool_name: update_values,
        product_scope: write,
        risk: high,
        approval: per_call,
      }
    - {
        tool_name: write_append_values,
        product_scope: write,
        risk: high,
        approval: per_call,
      }
    - {
        tool_name: clear_values,
        product_scope: write,
        risk: high,
        approval: per_call,
      }
    - {
        tool_name: batch_update_spreadsheet,
        product_scope: write,
        risk: critical,
        approval: per_call,
      }
    - {
        tool_name: create_spreadsheet,
        product_scope: write,
        risk: high,
        approval: per_call,
      }
  unsupported_capabilities:
    - "Apps Script execution and chart image export are out of scope."
    - "No Drive-level file moves/permissions; cell + structural edits only."
```

Invariants this satisfies (loader — `profile_catalog.py`): HTTPS endpoint (`:106-114`); ≥1 callback mode (`:116-121`); every write/draft tool `approval: per_call` (`ConnectorToolPolicy._mutating_tools_require_per_call_approval`, `:75-84`); preview → `requires_preview_gate` (`:123-132`); profile-owned seed → `requires_pre_registered_client` + non-colliding `server_id` (`_assert_installable_server`, `:243-267`); `connector_slug` is a real marketing slug (`reconcile`, `:224-228`).

### 3.3 `oauth_coordinator.py` (code)

```python
def start(
    self, *, slug: str, org_id: str, user_id: str,
    callback: DesktopOAuthCallback,
    requested_product_scope: Literal["read", "draft", "write"] = "read",  # + "write"
) -> DesktopStartResult: ...

def _requested_permissions(
    self, profile: DesktopConnectorProfile, requested_product_scope: str,
) -> tuple[str, ...]:
    # write implies read; draft implies read. Least-privilege by default.
    wanted = {"read"}
    if requested_product_scope == "draft":
        wanted.add("draft")
    elif requested_product_scope == "write":
        wanted.add("write")
    return tuple(p.identifier for p in profile.permissions if p.required_for in wanted)
```

### 3.4 `desktop_routes.py` (code)

```python
class DesktopStartOAuthRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    callback: _DesktopLoopbackCallbackModel | _DesktopDeepLinkCallbackModel = Field(..., discriminator="kind")
    requested_product_scope: Literal["read", "draft", "write"] = "read"   # + "write"
```

`_to_capability` (`:304-318`) already maps any non-read `product_scope` → `status="scope_required"`, `read_only=False` — no change; Sheets writes surface as scope-gated capabilities in the catalog card.

### 3.5 `connectors-desktop.ts` (contract)

```ts
export type DesktopRequestedProductScope = "read" | "draft" | "write";
```

### 3.6 FTUE surface (chat-surface) — port shape the P4 popover binds (reference, not built here)

The FTUE `connectors` port (README §3.1) that the P4 tools popover and the Settings connectors destination both drive:

```ts
interface OnboardingConnectorsPort {
  listDesktopCatalog(): Promise<DesktopConnectorCatalogResponse>; // GET /v1/connectors/desktop/catalog
  connect(
    slug: ConnectorSlug,
    scope: DesktopRequestedProductScope,
  ): // 1-click → system-browser OAuth
  Promise<DesktopConnectorConnectionResult>; // POST …/{slug}/desktop/start-oauth (+ loopback listener → …/desktop/oauth-callback)
}
```

For Sheets the popover calls `connect("gsheets", "write")` (the FTUE copy promises "read & write workbooks", SPEC.md:35). The host binder owns the loopback listener / deep-link (desktop main process) — the shared surface only calls the port.

---

## 4. Precise wiring into the real code

1. **Catalog reconciliation (no code).** `DesktopProfileCatalog.load()` at boot (`app.py:1791`) validates the new profile; `reconcile()` (`profile_catalog.py:201-241`) joins it to the `gsheets` marketing card and asserts installability. The row then flows through the facade proxy `GET /v1/connectors/desktop/catalog` (`connector_routes.py:52,159-167`) → `desktop_catalog` (`desktop_routes.py:168-191`) → `_to_catalog_entry` (`:278-301`), listing read tools as `supported` and write tools as `scope_required`.

2. **Write-scope OAuth start.** 1-click connect posts to `POST /v1/connectors/{slug}/desktop/start-oauth` (`desktop_routes.py:193-228`) → `coordinator.start(..., requested_product_scope="write")` → `_requested_permissions` (patched §3.3) returns `[spreadsheets.readonly, spreadsheets]` → drives `McpRegistryService.start_auth` (`service.py:450-509`) which builds the PKCE authorization URL via `RemoteMcpOAuthClient.authorization` (`mcp_oauth.py:123-156`). **This is the load-bearing change:** without §3.3/§3.4, `write` is rejected by the `Literal` and the `spreadsheets` scope is never in the auth request — exactly the latent gap that leaves Drive's own `drive.file` write scope unreachable today (`oauth_coordinator.py:303-305`).

3. **Pre-registered client = operator setup, not code.** The `spreadsheets` scope needs a Google OAuth client. That `client_id`/`client_secret` is injected at install into the MCP record's `oauth_client` (`service.py:_oauth_client_config`, `:693-715`; encrypted via `TokenVault`) and consumed by `RemoteMcpOAuthClient._apply_configured_oauth_client` (`mcp_oauth.py:269-296`). It is **never committed**. If absent, `start_auth` raises `McpOAuthError` → coordinator maps to `connector_oauth_setup_required` → HTTP 409 (`desktop_routes.py:59`, `oauth_coordinator.py:171-181`) — a graceful "needs setup" card, not a crash. This is the clean product/operator boundary.

4. **Token exchange + storage (no code).** `desktop/oauth-callback` (`desktop_routes.py:230-265`) → `coordinator.complete` (owner-match / confused-deputy defense, `:199-247`) → `McpRegistryService.complete_auth` (`service.py:511+`) exchanges the code and stores encrypted access+refresh in `TokenVault`. Response carries only safe metadata.

5. **Per-call write approval (no code — reuse the graph HITL).** In the runtime, every `call_mcp_tool` invocation is interrupted: `_native_interrupt_config` (`factory.py:420-428`) registers `call_mcp_tool` with `allowed_decisions=["approve","edit","reject"]` on the same `HumanInTheLoopMiddleware` that gates workspace writes. The worker projects each interrupted action into `approval_requested` with `approval_kind="mcp_tool"` (`stream_events.py:766-834`), classifying read-vs-write by tool NAME via `_connector_action_is_read_only` (`:910-918`) → `ApprovalCategory.WRITE` + `risk="medium"` for writes → the FE `ConnectorConsentCard` renders a write consent. **The profile's `approval: session|per_call` field is the declared contract + the scope gate; the actual per-call gate is graph-level.**

6. **Tool-naming constraint (grounded gotcha).** `_connector_action_is_read_only` (`stream_events.py:913-916`) treats a name as write only if it contains `create/post/send/update/delete/write` — **`append` is not in the list.** A bare `append_values` would render a _read_ consent card despite mutating the sheet. The §3.2 profile therefore names it `write_append_values` (and uses `update_values`, `clear_values`, `create_spreadsheet`, `batch_update_spreadsheet`). Alternative: add `append` to the classifier set — see Open questions.

7. **Per-chat scoping (no code).** Pausing Sheets for a chat rides the existing `paused_connectors` gate: `PATCH /v1/agent/conversations/{id}/connectors` (facade `app.py:477`) → `McpPermissionPolicy.is_server_card_authorized` denies the paused `server_id` for both card listing and `call_mcp_tool` re-check (`permissions.py:44`, `call_tool.py:83-92`). First agent use of an unauthenticated server raises the `mcp_auth_required` interrupt (`auth_mcp.py:80-93`).

---

## 5. Parity notes (design classes → design-system tokens/primitives, per SPEC.md)

The Sheets connector renders inside surfaces that are P4's (FTUE tools popover) and the shipped Settings connectors destination — this PRD adds no bespoke CSS. Parity is inherited:

- **FTUE tools popover row** (SPEC.md:35 — `Google Sheets` / "read & write workbooks", 1-click, `connected` on select; group note "1-click connect · you approve first use"). Reuse the shared `ToolPicker`/connector-row primitives (`packages/chat-surface/src/composer/`), never re-author. Row label from `display_name`; subtitle from `description`; the write-approval promise maps to the existing `ApprovalCard`/consent-card family (`packages/chat-surface/src/approvals/`).
- **Tokens** (README §2 map): row surface `--color-surface`/`--color-bg-elevated`; hairline `--color-border`; `connected` state uses the jade success token `--color-success` (design `--jade`); the accent check/affordance is sky `--color-accent` only — no second accent (SPEC.md:46 "sky-only"). Mono metadata labels (scope strings) use `--font-mono` at the popover's `--font-size-2xs`.
- **Provider swatch** (Google multicolor) is _data_, not the app accent — carry it as an inline swatch value like the design's per-provider dots (README §2, SPEC.md:33), consistent with `icon_hint: gsheets` resolving to the frontend icon registry (`apps/frontend/src/features/connectors/adapters.ts:168,195`).
- **Settings connectors destination**: the row is read-only catalog chrome (`_to_catalog_entry`); write tools show as `scope_required` capability pills — inherits the destination's existing token-mapped card styling. No new classes.

---

## 6. Test list

**Unit — backend (`services/backend/.venv`)**

- `test_desktop_profiles.py`: (a) `google-sheets` reconciles to a `gsheets` marketing card and is non-orphan; (b) both `spreadsheets.readonly` (read) and `spreadsheets` (write) permissions present; (c) every `product_scope: write` tool has `approval: per_call` (guarded by the loader; add a negative case mutating one to `session` → `ProfileCatalogError`); (d) `server_id` `desktop:google:sheets` collides with no seed.
- `test_desktop_oauth.py`: (a) `start(requested_product_scope="write")` → `requested_permissions` contains the `spreadsheets` scope **and** the read scope; (b) `"read"` omits the write scope (least-privilege); (c) with no operator `oauth_client`, write connect raises `connector_oauth_setup_required`; (d) preview gate: write connect blocked as `connector_preview_disabled` when `preview_enabled=False`.
- `test_desktop_routes.py`: start-OAuth route 200 for `requested_product_scope:"write"`; 422 for an unknown scope (`extra="forbid"` + `Literal`).

**Unit — contracts**

- `connectors-desktop.test.ts`: `"write"` assignable to `DesktopRequestedProductScope`; `api-types` typecheck green.

**Live-stack (per README §9 / `docs/plan/verification/`)**

- Desktop OAuth loopback connect against a **fake Sheets MCP** (pre-registered client stub): `desktop/start-oauth (write)` → loopback callback → `authenticated`; assert access+refresh land encrypted in `TokenVault` and the token never appears on any wire (`DesktopConnectionResult` only).
- Hermetic real-graph run (deterministic fake model, per the verification keystone) that calls `update_values`: assert exactly one `approval_requested` with `approval_kind="mcp_tool"`, `category="write"`, `read_only=false`; on `approve`, the tool executes; on `reject`, it does not.
- Read parity: a `get_values` call surfaces as an approval with `category="read"`/`read_only=true` (session-style), proving the classifier split.

---

## 7. Acceptance criteria

1. `desktop_profiles.yaml` + `catalog.yaml` load and reconcile at boot; `GET /v1/connectors/desktop/catalog` returns a `gsheets` row with read tools `supported`, write tools `scope_required`.
2. `POST /v1/connectors/{slug}/desktop/start-oauth {requested_product_scope:"write"}` returns an authorization URL whose scope set includes `https://www.googleapis.com/auth/spreadsheets`; `"read"` returns only `spreadsheets.readonly`.
3. Absent operator OAuth-client credentials, connect fails gracefully with `connector_oauth_setup_required` (409) — no 500, no secret leak.
4. After connect, an agent read (`get_values`) returns cell data; an agent write (`update_values`) pauses on an `mcp_tool` **write** approval and only mutates the sheet after explicit `approve`. Claude never writes without the human approval.
5. Per-chat pause of `gsheets` blocks both card visibility and any `call_mcp_tool` to it (defense-in-depth re-check).
6. No committed secret, endpoint client-id, or token anywhere; `api-types` + backend + facade typecheck/tests green; the change is path-filtered to connectors.
7. Parity: the Settings connectors row and (when P4 lands) the FTUE popover row render from the catalog with tokens per SPEC.md, sky-only accent, jade `connected`.

---

## 8. Risks / edge-cases

- **Endpoint reality (highest).** The design pins `sheetsmcp.googleapis.com/mcp/v1` as an _operator-confirmed_ endpoint. If no official Google Sheets MCP with cell writes exists at ship time, the row stays honestly `preview`/needs-setup (loader + `_assert_available` fail closed) rather than pretending to work. The fallback — a self-hosted Sheets MCP server wrapping Sheets REST v4 — is a **separate net-new service** (own venv/Dockerfile/deploy per the service-boundary rules) and exceeds P6b; flag for a decision, don't smuggle it in.
- **Ship availability vs FTUE promise.** Preview profiles require `DESKTOP_CONNECTORS_ALLOW_PREVIEW=true` (`app.py:1787-1789`) to connect. If the FTUE offers Sheets as 1-click in the default desktop build, either (a) enable preview in the desktop supervisor env, or (b) promote to `release_stage: stable` once a verified endpoint + shipped client exist. Until then the popover row must degrade to "connect in Settings / needs setup", not a dead button.
- **Latent write-scope bug is now exercised.** `_requested_permissions` never requesting `write` was dormant (no write connector shipped). Extending it is correct, but audit that no caller assumed `draft` was the max scope; the `elif` keeps `read`/`draft` byte-identical.
- **Classifier naming coupling.** The read/write consent split depends on tool names containing write-terms. If the real MCP server names a write tool `append_values` (no write-term), it will render a read consent — a security-relevant misclassification. Mitigation in §3.2 (name it `write_append_values`); durable fix is adding `append`/`insert`/`set`/`clear` to `_connector_action_is_read_only` (a small, separately-testable ai-backend change).
- **Session vs per_call fidelity.** The profile declares `approval: session` for reads and `per_call` for writes, but the runtime currently interrupts on _every_ `call_mcp_tool`. Reads therefore also prompt (as read-category cards) rather than being silently session-scoped. Acceptable for v1 (safer), but note the declared contract is not yet differentially enforced — don't claim "reads never prompt".
- **Scope downgrade / re-auth.** A user who connected `read` then wants `write` triggers a fresh start-OAuth at `write`; `_ensure_server` (`:264-296`) is idempotent and won't clobber the existing record, but the new token must overwrite — verify `complete_auth` replaces rather than appends the token envelope for the same `server_id`.
- **`extra="forbid"` wire strictness.** Old desktop clients posting without `requested_product_scope` still default to `read` (safe); new `write` value is additive and non-breaking for the response shape.

---

## Open questions

- Endpoint decision: is there an official Google Sheets MCP endpoint with cell/formula WRITE tools to pin, or do we ship a self-hosted Sheets MCP server (Sheets REST v4 wrapper)? The latter is a new deployable service (own venv/Dockerfile/deploy) and exceeds P6b — needs an explicit product+eng decision. The profile YAML endpoint is a placeholder until confirmed.
- Release stage for the FTUE: does Sheets ship as `preview` (requires DESKTOP_CONNECTORS_ALLOW_PREVIEW=true to connect, so the FTUE 1-click degrades to needs-setup) or `stable` (only defensible once a verified endpoint + a shipped/operator pre-registered client exist)? This gates whether the FTUE popper's Sheets button actually connects in the default desktop build.
- Operator OAuth client: who provisions the Google Cloud OAuth client (client_id/secret) for the `spreadsheets` scope, and is it a per-deployment operator secret injected at install, or a shipped 0xCopilot-owned client? Sheets write is a sensitive scope; confirm the consent-screen ownership and verification status.
- Classifier fix: name write tools with recognized write-terms (mitigation in this PRD) OR extend `_connector_action_is_read_only` to include append/insert/set/clear? The latter is a small ai-backend change that also fixes future connectors but touches shared runtime code.
- Should reads be truly session-scoped (no repeat prompt) to honor the profile's `approval: session`, i.e. teach the graph HITL to differentiate session vs per_call by the profile tool policy? Currently every call_mcp_tool interrupts; reads prompt too. Decide whether v1 accepts the safer over-prompt or invests in differential enforcement.
- Security sign-off (P6 gate): does Sheets write need a diff/preview of the target range + value before the approval card resolves (analogous to the Safe tx simulation ask), or is the range+value in the consent card params sufficient for v1?

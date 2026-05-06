# PR 6.1 — Conversation sharing schema + create flow

> **Status:** Draft · PRD + Spec + Architecture
> **Plan reference:** Wave 6, PR 6.1 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** ai-backend (write + read paths) · backend-facade (proxy + tokenized public route) · frontend (`/share/:token` recipient view + ShareButton popover)
> **Size:** L · One new table, one new join table, six routes, one read-only frontend route. ~700 net LOC including tests.
> **Reads alongside:** [`docs/architecture/runtime-stream-handshake.md`](../architecture/runtime-stream-handshake.md), [`docs/architecture/service-boundaries.md`](../architecture/service-boundaries.md), [`docs/decomp/persistence/_index.md`](../decomp/persistence/_index.md), Atlas Design Doc handoff bundle (§Flow — Share, §Decisions log).
> **Sibling docs (sequencing):**
> – [PR 1.2 — Per-chat connector scope](pr-1-2-per-chat-connector-scope.md) (landed: `enabled_connectors` JSONB)
> – [PR 1.6 — Workspace defaults + conversation lifecycle](pr-1.6-workspace-defaults-conversation-lifecycle.md) (forward-declares `parent_conversation_id`, `folder`, `deleted_at` on `agent_conversations`)
> – [PR 3.1 — Citation chips + Sources tab](pr-3.1-citation-chips-sources-tab.md) (provides `CitationChip`; this PR adds a `restricted` variant)
> – [PR 4.5 — Usage overlay + Share popover](pr-4.5-usage-overlay-share-popover.md) (lands the placeholder ShareButton; this PR fills it)
> – [PR 6.2 — Fork mechanic](pr-6.2-conversation-fork.md) (depends on the recipient view + share row this PR creates)

---

## 1 · PRD

### 1.1 Problem

The Atlas design doc (§Flow — Share) requires three behaviours that we have **none** of today:

1. **Create a share** of an existing conversation — either workspace-scoped ("anyone in workspace") or to specific people, optionally with a copyable link, optionally with snippets visible to the viewer or just citation placeholders.
2. **Recipient opens the share** and sees a faithful, read-only rendering of the conversation thread — citations, drafts, sources, subagent activity — using their own login, with sources they don't have access to rendered as `Source restricted` per the Decisions log ("Atlas does not exfiltrate facts to people who shouldn't see them").
3. **A creator or admin can revoke a share** at any time, with audit-grade evidence of who shared what, when, with whom, and what the recipient could and could not see.

The existing codebase has zero share, public-link, or snapshot primitives. The closest prior art is the workspace invitations endpoint (`POST /v1/auth/invitations/{token}/accept` in [`workspace_routes.py:103`](../../services/backend-facade/src/backend_facade/workspace_routes.py)) and the SCIM token hashing pattern (`scim_tokens.token_hash` in [`migrations/0015_scim.sql:34-49`](../../services/backend/migrations/0015_scim.sql)). We adopt both verbatim — no new token primitive.

### 1.2 Goals

1. **A share is a row, not a service.** One `conversation_shares` row + an optional recipients join table. Snapshot semantics: the row carries `snapshot_at` and the recipient view returns messages strictly older than it.
2. **A bearer link is the same row + a hashed token.** No separate "share*token" subsystem. The plaintext token is returned **once** at create time; we store `sha256(plaintext)` in the row exactly like `scim_tokens.token_hash`. No TokenVault / Fernet (those are for tokens we need to \_re-present*, like OAuth refresh tokens).
3. **Anyone-with-the-link still authenticates.** v1 has no anonymous public link. The recipient must have a valid Atlas session; the token is the _grant_, not the identity. This is the same model as workspace invitations.
4. **Read-only is the read-only path.** The recipient view does **not** start runs, send messages, or stream. It reuses `listMessages`, `replayRunEvents`, `listSources`, `listDrafts`, and `listSubagents` — already shipped — projected through a thin server-side filter that respects `snapshot_at` and `sources_visible_to_viewer`.
5. **Streaming, agent harness, capabilities middleware are untouched.** No new event type. No new tool. The harness has nothing to do with sharing — it produces events; sharing is a read filter on those events.
6. **Audit at every privileged write.** `conversation.share.{created,updated,revoked,recipient_added,recipient_removed,viewed,view_denied}` rows go into `runtime_audit_log` (existing org-scoped HMAC chain in [`migrations/0003_audit_hardening.sql`](../../services/ai-backend/migrations/0003_audit_hardening.sql)). No new chain.
7. **Forward-declare for fork (PR 6.2).** `share_id` rides in the recipient response so PR 6.2's `POST /shares/{token}/fork` can stamp `forked_from_share_id` without a second round-trip.

### 1.3 Non-goals (this PR)

- **Anonymous public links.** v1 requires the recipient to be a logged-in Atlas user. (Easy to relax later by inverting one branch in the auth dependency; deliberate v1 restraint to keep RLS + audit posture clean.)
- **Cross-org shares.** A share's `org_id` equals the source conversation's `org_id`; recipient identity must resolve to the same org. Cross-org delegation needs a trust contract we don't have.
- **Live streaming for recipients.** The recipient view is a snapshot at `snapshot_at`. If the source thread is still running, the recipient sees up to that moment. Refreshing re-fetches but the snapshot does not advance — the share row's `snapshot_at` is immutable. (To "share latest", create a new share. Cheap.)
- **Per-message ACL.** v1 has one boolean — `sources_visible_to_viewer` — that gates citation snippets and the Sources tab payload globally. Per-source ACL (where source X is visible but source Y is restricted because the recipient lacks Slack OAuth) is a meaningful enhancement and lands in PR 6.1.1 once we have a connector-membership lookup; v1 is "all snippets visible" or "all snippets restricted."
- **Comments / reactions on the recipient view.** Out of scope; the design doesn't ask for it.
- **Share notifications via email/Slack.** The notification port is wired (PR 1.4 added `NotificationDispatcher` in [`agent_runtime/api/notifications.py`](../../services/ai-backend/src/agent_runtime/api/notifications.py)); this PR fires `notify_share_created` if the dispatcher is available. The Settings → Notifications matrix (PR 4.1) controls fan-out — when not built yet, the recipient sees the share in their inbox bus only.
- **Fork mechanics.** Schema + endpoint live in PR 6.2. This PR's `share_id` is the input; the FK self-reference + `forked_from_share_id` column are PR 6.2 migrations.

### 1.4 Success criteria

| #     | Criterion                                                                                                                                                                                                                                                            | Verified by                                                                                   |
| ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- |
| AC-1  | `POST /v1/agent/conversations/{id}/share` with `view_access="workspace"` returns a `share_id`, a one-time `share_token` (plaintext), a `share_url`, and the materialised view (`{view_access, recipients: [], expires_at, sources_visible_to_viewer, snapshot_at}`). | Unit + integration test against `RuntimeApiService.create_share`                              |
| AC-2  | The same call with `view_access="specific"` writes one `conversation_share_recipients` row per `recipients[]` item in the same TX as the share row.                                                                                                                  | Unit test                                                                                     |
| AC-3  | `GET /v1/agent/shares/{share_token}` resolves to the share row in O(1) via `idx_conversation_shares_token_hash`, returns 404 when revoked/expired/missing, returns 403 if the calling identity isn't an authorised recipient.                                        | API contract test                                                                             |
| AC-4  | The recipient response includes `messages[]` filtered to `created_at <= snapshot_at AND deleted_at IS NULL`, `events[]` filtered the same way, `sources[]` from `runtime_citations` (PR 1.1), `drafts[]` (PR 1.3), `subagents[]` (PR 1.5).                           | Snapshot-fidelity test                                                                        |
| AC-5  | When `sources_visible_to_viewer = false`, the response replaces every citation `snippet`, `url`, and `title` with `null` and a `restricted: true` marker. The FE renders `<CitationChip restricted />`.                                                              | API + FE component test                                                                       |
| AC-6  | `DELETE /v1/agent/shares/{share_id}` sets `revoked_at = now()`. Subsequent `GET /shares/{token}` returns 404. Audit row `conversation.share.revoked` written.                                                                                                        | Persistence + audit test                                                                      |
| AC-7  | A **cross-org** caller (header `x-enterprise-org-id` ≠ share `org_id`) gets 404 (not 403 — same shape as other tenant data; never leaks existence). RLS on `conversation_shares` blocks at the SQL layer.                                                            | Cross-org security test                                                                       |
| AC-8  | Streaming handshake byte-identical pre/post merge. No new `event_type` in `runtime_events`. `RuntimeEventEnvelope` Pydantic schema unchanged. PRs 1.1 / 1.3 / 1.4 / 1.5 / 1.6 in flight produce no merge conflict.                                                   | Schema regression test                                                                        |
| AC-9  | Audit chain verifier ([existing](../../services/ai-backend/migrations/0003_audit_hardening.sql)) passes for the new actions. SIEM exporter pumps the new actions through unchanged.                                                                                  | Audit integration test + dry-run SIEM export against the new actions                          |
| AC-10 | The plaintext `share_token` appears in exactly one log line — the create response — and never in any other log, audit row, or event. `token_hash` is what's stored. The token_prefix (first 8 chars, like SCIM) is the only hint surfaced for UI.                    | Token leakage test (grep on captured logs in `tests/integration/test_share_token_leakage.py`) |

### 1.5 User stories

| #    | Persona                                      | Story                                                                                                                                                                                                                                                                            |
| ---- | -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-1 | Sarah (creator)                              | I open the Share popover, pick "anyone in workspace", flip "sources visible to viewer" off, click Create. I get a copy-link toast. The popover lists my one active share with the partial token (`s_LtA4y…`) and a Revoke button.                                                |
| US-2 | Marcus (workspace recipient)                 | I paste Sarah's link. I land on `/share/{token}`. I see her conversation thread, read-only, no composer, citations are placeholders, the topbar reads "Shared by Sarah · read-only · open in your own chat →".                                                                   |
| US-3 | Sarah revokes                                | I click Revoke. The share row's `revoked_at` is set. Marcus's tab, on next refresh, returns 404 with copy "This share has been revoked."                                                                                                                                         |
| US-4 | Devi (specific-people recipient)             | Sarah shared with me explicitly. I open the link from Slack DM. Same recipient view as Marcus, but the topbar reads "Shared by Sarah with you" (i.e. specific). If I forward the link to Priya, Priya gets 403 ("This share isn't for you").                                     |
| US-5 | Workspace admin reviewing audit              | I export the audit log; one share creation produced one row (`conversation.share.created`) with metadata `{share_id, view_access, recipient_user_ids, sources_visible_to_viewer, has_token, expires_at}`. Each `view` produced a `conversation.share.viewed` row with viewer id. |
| US-6 | A recipient who lacks org access             | Someone outside the org receives the link. Their session resolves to a different `org_id`. The recipient view returns 404 (not 403 — the share's existence isn't leaked across org boundaries).                                                                                  |
| US-7 | Sarah's chat receives a new turn after share | The recipient refresh sees only messages older than `snapshot_at`. Sarah re-shares to advance the snapshot.                                                                                                                                                                      |

### 1.6 Risks

| Risk                                                                                                                                      | Mitigation                                                                                                                                                                                                                                                                                                                                    |
| ----------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Share token leaks via logs / metrics / audit metadata                                                                                     | The plaintext token is returned once, in the create response. The store layer accepts hashes only (typed wrapper `ShareTokenSecret`); the stringification of `ShareTokenSecret` returns `…<prefix>` so misuse in `logger.info(f"{token=}")` produces a redacted form. Test `test_share_token_leakage.py` greps captured logs to enforce this. |
| A recipient resolves to multiple shares (same conversation, multiple links)                                                               | The token is the lookup key — `share_token_hash` is unique. Multiple shares per conversation are independent rows; revoking one doesn't affect others.                                                                                                                                                                                        |
| Snapshot drift (recipient sees stale data)                                                                                                | Documented behaviour, not a bug. The Share popover's "Refresh share" action creates a new share row with a newer `snapshot_at` and revokes the old token in the same TX. This is faster and clearer than mutating snapshot_at, and audit-cleaner.                                                                                             |
| Citations leak through `events[]` even when `sources_visible_to_viewer = false`                                                           | The recipient endpoint runs every event payload through a one-pass redactor (`_redact_event_for_recipient`) that strips `snippet`, `url`, `title`, `excerpt` from `source_ingested`, `tool_call`, and `presentation` payloads. Tests assert by reflection that no leaked fields make it back.                                                 |
| RLS bypass via the recipient endpoint (recipient session sets `app.current_org_id`; we then need to look up a foreign-org share by token) | The token-lookup path is intentionally **org-agnostic**: it resolves the share via `share_token_hash` (unique global), then sets `SET LOCAL app.current_org_id = share.org_id` and re-checks recipient membership against backend's user list. Only after that do we read messages — which RLS now matches.                                   |
| Active run in source while recipient views                                                                                                | Recipient sees up to `snapshot_at`. New events past that are server-filtered out. Re-share to advance the snapshot. No race.                                                                                                                                                                                                                  |
| Recipient's session has no `org_id` (rare; first-login flow)                                                                              | Endpoint returns 401 with a redirect hint to the login page, preserving the share URL.                                                                                                                                                                                                                                                        |
| Replay endpoint streams beyond snapshot                                                                                                   | The recipient never calls `/v1/agent/runs/{id}/stream` — the recipient endpoint has its own bounded `events` selection. We do not expose SSE for shared content in v1. (We could later, with a `?after_sequence=&max_sequence=` clamp; not needed for v1's snapshot semantics.)                                                               |

### 1.7 Unit testing requirements

Tests live with the producing module per [`services/ai-backend/tests/CLAUDE.md`](../../services/ai-backend/tests/CLAUDE.md).

- `tests/unit/agent_runtime/persistence/records/test_shares.py` — `ShareRecord` validation (token format, view_access enum, snapshot_at not in the past), recipients invariants.
- `tests/unit/runtime_adapters/in_memory/test_share_store.py` — create / get-by-token / list-by-conversation / revoke / expire ordering.
- `tests/unit/runtime_adapters/postgres/test_share_store.py` — RLS round-trip (cross-org reads return None), unique-token-hash conflict surface, foreign-key cascade behaviour on conversation soft-delete (share row stays; recipient view returns 404 because conversation is gated).
- `tests/unit/runtime_api/services/test_share_service.py` — create / update / revoke happy paths; permission denials; token mint determinism (one and only one `share_token` per response); audit emission.
- `tests/unit/runtime_api/services/test_recipient_view.py` — workspace vs specific access matrix; `sources_visible_to_viewer` redaction; snapshot filtering across messages, events, drafts, sources, subagents.
- `tests/unit/runtime_api/http/test_share_routes.py` — route shape; identity propagation; tokenised public-route auth dependency wiring.
- `tests/integration/test_share_audit_chain.py` — cross-share-action chain extension; verifier passes.
- `tests/integration/test_share_token_leakage.py` — captures logs from a full create-and-view cycle, asserts plaintext token absent everywhere except the create response body.

Frontend tests:

- `apps/frontend/src/features/share/SharePopover.test.tsx` — create / list / revoke; one-time token surfacing.
- `apps/frontend/src/features/share/ShareScreen.test.tsx` — read-only mode (no composer, no model picker); restricted-citation rendering; "Shared by … · read-only" header.
- `apps/frontend/src/api/agentApi.test.ts` (extended) — `createShare`, `getSharedConversation`, `revokeShare` shapes.

---

## 2 · Spec

### 2.1 Wire — share lifecycle (creator surface)

All routes are exposed by `backend-facade` under `/v1/agent/...` and proxied to ai-backend at `/internal/v1/agent/...`. Identity headers are injected by `FacadeAuthenticator.service_headers()` ([`workspace_routes.py:149`](../../services/backend-facade/src/backend_facade/workspace_routes.py)). No facade-level business logic.

| Verb     | Path                                               | Auth                                                         | Effect                                                                                                                           |
| -------- | -------------------------------------------------- | ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| `POST`   | `/v1/agent/conversations/{conversation_id}/share`  | Identity (creator must own conversation OR be `ADMIN_USERS`) | Create a share. Returns the materialised view + plaintext token + URL. **Plaintext token returned once.**                        |
| `GET`    | `/v1/agent/conversations/{conversation_id}/shares` | Identity (owner / admin)                                     | List active shares created on this conversation. No tokens in response (only `share_id`, `token_prefix`).                        |
| `PATCH`  | `/v1/agent/shares/{share_id}`                      | Identity (creator / admin)                                   | Update mutable fields: `recipients`, `expires_at`, `sources_visible_to_viewer`. RFC 7396 merge-patch semantics (PR 1.6 pattern). |
| `DELETE` | `/v1/agent/shares/{share_id}`                      | Identity (creator / admin)                                   | Revoke. Sets `revoked_at`. Returns 204.                                                                                          |

#### 2.1.1 `POST` request

```jsonc
{
  "view_access": "workspace" | "specific",
  "recipient_user_ids": ["user_…"],          // required when view_access="specific"; else []
  "sources_visible_to_viewer": false,
  "expires_at": "2026-06-05T00:00:00Z" | null,
  "include_link": true                        // when false, the share row has no token (recipient table only)
}
```

#### 2.1.2 `POST` response

```jsonc
{
  "share_id": "share_01HZ…",
  "share_token": "s_3f7b2c9a04…", // plaintext, ONE TIME ONLY
  "share_token_prefix": "s_3f7b2c9",
  "share_url": "https://atlas.acme.com/share/s_3f7b2c9a04…",
  "view_access": "workspace",
  "recipient_user_ids": [],
  "sources_visible_to_viewer": false,
  "snapshot_at": "2026-05-05T18:01:14.220Z",
  "expires_at": null,
  "created_at": "2026-05-05T18:01:14.220Z",
  "created_by_user_id": "user_…",
}
```

#### 2.1.3 `GET /shares` response (no tokens)

```jsonc
{
  "shares": [
    {
      "share_id": "share_01HZ…",
      "share_token_prefix": "s_3f7b2c9",
      "view_access": "workspace",
      "recipient_user_ids": [],
      "sources_visible_to_viewer": false,
      "snapshot_at": "…",
      "expires_at": null,
      "revoked_at": null,
      "created_by_user_id": "user_…",
      "created_at": "…",
      "view_count": 4, // best-effort counter from audit; not persisted on row
    },
  ],
}
```

### 2.2 Wire — recipient surface

| Verb   | Path                                  | Auth                                                                                                 | Effect                                                                                                         |
| ------ | ------------------------------------- | ---------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `GET`  | `/v1/agent/shares/{share_token}`      | Identity (any logged-in user; **org_id of session must match share.org_id and pass recipient gate**) | Return read-only conversation snapshot + filtered messages, events, sources, drafts, subagents.                |
| `POST` | `/v1/agent/shares/{share_token}/view` | Same as above                                                                                        | Audit emission `conversation.share.viewed`. Idempotent in a 60-second window per `(share_id, viewer_user_id)`. |

The token-only path is exposed under the same `/v1/agent/shares/...` proxy. The facade does **not** treat it specially — it passes identity headers as for any other route. The recipient must be authenticated; the token grants access to the _share row_, not the _user identity_.

#### 2.2.1 `GET /shares/{share_token}` response

```jsonc
{
  "share": {
    "share_id": "…",
    "view_access": "workspace",
    "sources_visible_to_viewer": false,
    "snapshot_at": "…",
    "shared_by": { "user_id": "…", "display_name": "Sarah Chen" },
  },
  "conversation": {
    "conversation_id": "…",
    "title": "FY26 Q1 launch announcement draft",
    "created_at": "…",
    "folder": "Launches",
  },
  "messages": [
    /* ChatMessage[]; created_at <= snapshot_at */
  ],
  "events_by_run_id": {
    "run_…": [
      /* RuntimeEventEnvelope[]; sequence_no within snapshot bound */
    ],
  },
  "sources": [
    /* CitationCard[] — title/snippet/url=null when restricted */
  ],
  "drafts": [
    /* Draft[] — content visible per same flag (drafts are first-party content, gated identically) */
  ],
  "subagents": [
    /* SubagentSummary[] */
  ],
}
```

The shape is the _intersection_ of contracts already in `packages/api-types`: `Conversation`, `Message`, `RuntimeEventEnvelope`, `CitationCard` (PR 1.5), `Draft` (PR 1.3), `SubagentSummary` (PR 1.5). One new wrapper type: `SharedConversationView`. Zero new low-level shapes.

### 2.3 Persistence

#### 2.3.1 `migrations/0022_conversation_shares.sql`

```sql
-- One row per share. Plaintext token is never stored; sha256(plaintext) lives
-- in share_token_hash, identical to the scim_tokens pattern.
--
-- snapshot_at is immutable: to "share latest" you create a new row + revoke
-- the old. This is faster than mutating + re-deriving recipient state and
-- gives audit a clean "share advanced" record (revoked + created pair).

CREATE TABLE IF NOT EXISTS conversation_shares (
    share_id                    TEXT PRIMARY KEY,
    org_id                      TEXT NOT NULL,
    conversation_id             TEXT NOT NULL
                                  REFERENCES agent_conversations(id) ON DELETE CASCADE,
    created_by_user_id          TEXT NOT NULL,
    view_access                 TEXT NOT NULL
                                  CHECK (view_access IN ('workspace', 'specific')),
    sources_visible_to_viewer   BOOLEAN NOT NULL DEFAULT false,
    share_token_hash            TEXT,                       -- sha256(plaintext); NULL = no link
    share_token_prefix          TEXT,                       -- first 8 chars for UI list
    snapshot_at                 TIMESTAMPTZ NOT NULL,
    expires_at                  TIMESTAMPTZ,                -- NULL = no expiry
    revoked_at                  TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT conversation_shares_token_consistency
      CHECK (
        (share_token_hash IS NULL AND share_token_prefix IS NULL) OR
        (share_token_hash IS NOT NULL AND share_token_prefix IS NOT NULL)
      ),
    CONSTRAINT conversation_shares_recipients_consistency
      -- Specific access requires at least one recipient row (enforced at write time
      -- by the service; this CHECK is documentation, not enforcement, since recipients
      -- live in a side table).
      CHECK (TRUE)
);

-- Token lookup is O(1); only rows with a token are indexed (most shares may be
-- people-only without a copy-link).
CREATE UNIQUE INDEX IF NOT EXISTS ux_conversation_shares_token_hash
    ON conversation_shares (share_token_hash)
    WHERE share_token_hash IS NOT NULL;

-- Creator's share list (for the popover).
CREATE INDEX IF NOT EXISTS idx_conversation_shares_active
    ON conversation_shares (org_id, conversation_id, created_at DESC)
    WHERE revoked_at IS NULL;

-- RLS — same pattern as every other tenant table (migration 0008).
ALTER TABLE conversation_shares ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON conversation_shares
    USING (org_id = current_setting('app.current_org_id', true));

-- Recipient join — one row per (share_id, user_id). Revoking the share cascades.
CREATE TABLE IF NOT EXISTS conversation_share_recipients (
    share_id                TEXT NOT NULL
                              REFERENCES conversation_shares(share_id) ON DELETE CASCADE,
    user_id                 TEXT NOT NULL,
    granted_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (share_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_share_recipients_user
    ON conversation_share_recipients (user_id);

ALTER TABLE conversation_share_recipients ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON conversation_share_recipients
    USING (EXISTS (
      SELECT 1 FROM conversation_shares s
       WHERE s.share_id = conversation_share_recipients.share_id
         AND s.org_id   = current_setting('app.current_org_id', true)
    ));
```

Rollback (`0022_conversation_shares.rollback.sql`) drops both tables.

#### 2.3.2 What we are _not_ adding

| Thing                                                                   | Why not                                                                                                                                                                                              |
| ----------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Per-message ACL column                                                  | The `sources_visible_to_viewer` boolean handles the design's stated requirement. Per-source ACL is a meaningful follow-up (PR 6.1.1) — not v1.                                                       |
| `view_count` column                                                     | Computed from audit (`conversation.share.viewed` rows) on demand. Not hot-path; avoids a write contender for the share row.                                                                          |
| Snapshot of message bodies into share                                   | The recipient endpoint reads live `agent_messages` filtered by `snapshot_at`. Cheaper, simpler, no encryption-version drift on the snapshot copy.                                                    |
| Foreign-key from `agent_conversations.parent_conversation_id` to itself | Belongs to PR 6.2 (with `forked_from_share_id`). One PR, one feature.                                                                                                                                |
| `share_link_url` column                                                 | Constructed from `share_token` + the deployment's `app_base_url` ([`backend_facade/deployment_profile.py`](../../services/backend-facade/src/backend_facade/deployment_profile.py)). No persistence. |
| Token expiry separate from share expiry                                 | The token is the share. One field, `expires_at`, gates both.                                                                                                                                         |

### 2.4 Token mint + verify

We adopt the SCIM pattern _exactly_. Reference: [`backend/migrations/0015_scim.sql:34-49`](../../services/backend/migrations/0015_scim.sql), [`backend/identity/scim_store.py:33-100`](../../services/backend/src/backend_app/identity/scim_store.py).

```python
# services/ai-backend/src/agent_runtime/api/share_token.py  (new, ~40 LOC)

import hashlib
import secrets

_TOKEN_PREFIX = "s_"
_TOKEN_BYTES = 24                # 32-char base32 body, 192 bits of entropy

class ShareTokenSecret(str):
    """A wrapped str that redacts on repr/format/log to prevent accidental leakage."""

    __slots__ = ()

    def __repr__(self) -> str:                 # noqa: D401
        return f"ShareTokenSecret({self.prefix()}…)"
    __str__ = __repr__                          # f"{token}" → redacted

    def prefix(self) -> str:
        return self[: len(_TOKEN_PREFIX) + 8]

    def expose(self) -> str:
        """Explicit unwrap. Only call sites: HTTP create response, FE share_url construction."""
        return super().__str__()

def mint_share_token() -> tuple[ShareTokenSecret, str, str]:
    body = secrets.token_urlsafe(_TOKEN_BYTES)
    plaintext = ShareTokenSecret(f"{_TOKEN_PREFIX}{body}")
    digest = hashlib.sha256(plaintext.expose().encode("utf-8")).hexdigest()
    return plaintext, digest, plaintext.prefix()

def hash_share_token(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
```

Why **not** TokenVault / Fernet ([`backend/token_vault.py`](../../services/backend/src/backend_app/token_vault.py)):

- Vault is for tokens we need to _re-present_ (OAuth refresh tokens, where the original plaintext must round-trip to a third party). We never need to re-present the share token: the recipient holds it, the server compares hashes.
- Vault inflates audit + adds a KMS dependency for no benefit.
- SCIM took the same call ([`migrations/0015_scim.sql:5`](../../services/backend/migrations/0015_scim.sql), comment `token_hash = sha256`); we re-use the precedent.

### 2.5 Audit

Six new `action` constants in the runtime audit emitter ([`runtime_worker/audit.py:32-40`](../../services/ai-backend/src/runtime_worker/audit.py)):

```python
class _ShareActions:
    SHARE_CREATED          = "conversation.share.created"
    SHARE_UPDATED          = "conversation.share.updated"
    SHARE_REVOKED          = "conversation.share.revoked"
    SHARE_RECIPIENT_ADDED  = "conversation.share.recipient_added"
    SHARE_RECIPIENT_REMOVED = "conversation.share.recipient_removed"
    SHARE_VIEWED           = "conversation.share.viewed"
    SHARE_VIEW_DENIED      = "conversation.share.view_denied"
```

Each row's `metadata_json_redacted`:

| Action                                            | Metadata (after redaction)                                                                                                                   |
| ------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| `conversation.share.created`                      | `{ share_id, conversation_id, view_access, recipient_count, sources_visible_to_viewer, has_token, token_prefix, expires_at, snapshot_at }`   |
| `conversation.share.updated`                      | `{ share_id, diff_keys, before, after }` (recipients diff'd as `{added: [...], removed: [...]}`)                                             |
| `conversation.share.revoked`                      | `{ share_id, conversation_id, view_access }`                                                                                                 |
| `conversation.share.recipient_added` / `_removed` | `{ share_id, user_id }`                                                                                                                      |
| `conversation.share.viewed`                       | `{ share_id, conversation_id, view_access, sources_visible_to_viewer }` — viewer is in the row's `actor_user_id`                             |
| `conversation.share.view_denied`                  | `{ share_token_prefix, reason: "expired"/"revoked"/"foreign_org"/"not_recipient" }` — never the conversation_id (the caller didn't reach it) |

The chain semantics ([`migrations/0003_audit_hardening.sql`](../../services/ai-backend/migrations/0003_audit_hardening.sql)) — per-org, append-only, HMAC `prev_hash` chain — apply unchanged. The verifier is unchanged. SIEM exporter ([`backend/siem_export/`](../../services/backend/src/backend_app/siem_export/)) pumps these new actions through with no code change.

`conversation.share.viewed` is **rate-limited** to one row per `(share_id, viewer_user_id, 60s)` to prevent log volume amplification on a refresh-loop. The dedupe key is computed in the API service before the audit append.

### 2.6 Permissions

| Caller                                                                   | Create | Get list | Update / Revoke | View (recipient endpoint)                                                                      |
| ------------------------------------------------------------------------ | ------ | -------- | --------------- | ---------------------------------------------------------------------------------------------- |
| Conversation owner                                                       | ✅     | ✅       | ✅ (own shares) | ✅ if also a recipient                                                                         |
| Workspace admin (`ADMIN_USERS` scope from `service-contracts/scopes.py`) | ✅     | ✅       | ✅              | ✅                                                                                             |
| Workspace member (other)                                                 | ❌     | ❌       | ❌              | ✅ iff `view_access="workspace"` (same org) **OR** named recipient on `view_access="specific"` |
| Foreign-org user                                                         | 404    | 404      | 404             | 404 (token lookup succeeds; recipient gate fails before the conversation lookup)               |

Owner check reuses [`runtime_api/services/conversations.py`](../../services/ai-backend/src/runtime_api/services)'s existing `_assert_conversation_owner_or_admin` helper that PR 1.6 introduces. Admin scope check reuses the same `ADMIN_USERS` constant PR 1.2.1 / 1.4 / 1.6 already touch. Recipient gate is a new helper `_recipient_gate(share, identity)` in `share_service.py`.

### 2.7 Error semantics

| Condition                                                               | Status | Code                        |
| ----------------------------------------------------------------------- | ------ | --------------------------- |
| Caller not owner / admin → `POST /share` or `PATCH/DELETE /shares/{id}` | 403    | `forbidden`                 |
| `view_access="specific"` with empty `recipient_user_ids`                | 422    | `recipients_required`       |
| Recipient not in same org                                               | 422    | `recipient_outside_org`     |
| `expires_at` in the past                                                | 422    | `invalid_expires_at`        |
| Share row not found / revoked / expired (creator endpoints)             | 404    | `share_not_found`           |
| Share row not found by token (recipient endpoint)                       | 404    | `share_not_found`           |
| Recipient not authorised on a `specific` share                          | 403    | `share_not_for_recipient`   |
| Cross-org access on any endpoint                                        | 404    | `share_not_found` (no leak) |
| Conversation soft-deleted (after share)                                 | 404    | `share_not_found`           |
| `include_link=false` on create + caller tries to fetch by token         | n/a    | (no token to fetch with)    |
| Concurrent `PATCH` adds two recipients with the same user_id            | 200    | (idempotent UPSERT)         |

### 2.8 Frontend contract (`@enterprise-search/api-types`)

```ts
// packages/api-types/src/index.ts  (additive)

export type ShareViewAccess = "workspace" | "specific";

export interface ConversationShare {
  share_id: string;
  share_token_prefix: string | null;
  view_access: ShareViewAccess;
  recipient_user_ids: string[];
  sources_visible_to_viewer: boolean;
  snapshot_at: string;
  expires_at: string | null;
  revoked_at: string | null;
  created_by_user_id: string;
  created_at: string;
  view_count?: number;
}

export interface CreateShareRequest {
  view_access: ShareViewAccess;
  recipient_user_ids?: string[];
  sources_visible_to_viewer: boolean;
  expires_at?: string | null;
  include_link: boolean;
}

export interface CreateShareResponse extends ConversationShare {
  share_token: string; // PLAINTEXT, ONE TIME ONLY
  share_url: string;
}

export interface UpdateShareRequest {
  recipient_user_ids?: string[];
  sources_visible_to_viewer?: boolean;
  expires_at?: string | null;
}

export interface SharedConversationView {
  share: {
    share_id: string;
    view_access: ShareViewAccess;
    sources_visible_to_viewer: boolean;
    snapshot_at: string;
    shared_by: { user_id: string; display_name: string };
  };
  conversation: Conversation; // existing type
  messages: Message[]; // existing
  events_by_run_id: Record<string, RuntimeEventEnvelope[]>; // existing
  sources: CitationCard[]; // existing (PR 1.5)
  drafts: Draft[]; // existing (PR 1.3)
  subagents: SubagentSummary[]; // existing (PR 1.5)
}
```

Five new functions in [`apps/frontend/src/api/agentApi.ts`](../../apps/frontend/src/api/agentApi.ts):

```ts
createShare(conversationId: string, request: CreateShareRequest, identity): Promise<CreateShareResponse>;
listShares(conversationId: string, identity): Promise<{ shares: ConversationShare[] }>;
updateShare(shareId: string, request: UpdateShareRequest, identity): Promise<ConversationShare>;
revokeShare(shareId: string, identity): Promise<void>;
getSharedConversation(shareToken: string, identity): Promise<SharedConversationView>;
```

`agentApi.ts` already proxies via `/v1/...`; these paths use `/v1/agent/shares/...` and `/v1/agent/conversations/{id}/share[s]`.

---

## 3 · Architecture

### 3.1 Where this lives in the system

```
                    ┌──────────────────────────────────────────────────────────┐
                    │  apps/frontend                                           │
                    │                                                          │
                    │   ChatScreen.tsx                  /share/:token route    │
                    │   └─ ShareButton (PR 4.5 ph)      └─ ShareScreen.tsx     │
                    │       └─ SharePopover.tsx              └─ ReadOnlyThread │
                    │           create / list / revoke           ↑             │
                    │                                            │             │
                    │   features/share/      ←── shared primitives reused      │
                    │     SharePopover, ShareScreen,  CitationChip (restricted)│
                    │     useSharedConversation hook  SourcesTab, ThreadBody   │
                    └──────────────────┬───────────────────────────────────────┘
                                       │  POST /v1/agent/conversations/{id}/share
                                       │  GET  /v1/agent/conversations/{id}/shares
                                       │  PATCH/DELETE /v1/agent/shares/{share_id}
                                       │  GET  /v1/agent/shares/{share_token}
                                       ▼
                          ┌────────────────────────────────────┐
                          │  backend-facade                    │  thin proxy.
                          │  share_routes.py (~80 LOC)         │  identity headers
                          │                                    │  injected; never
                          │  ALL routes require valid session  │  exposes /internal/v1
                          │  (no anonymous public link in v1). │
                          └────────────────┬───────────────────┘
                                           │ /internal/v1/agent/...
                                           ▼
                          ┌────────────────────────────────────┐
                          │  ai-backend  (runtime_api)         │
                          │                                    │
                          │  http/share_routes.py (~90 LOC)    │
                          │   ↳ ShareService (~180 LOC)        │
                          │       ├─ create_share              │
                          │       ├─ list_shares               │
                          │       ├─ update / revoke           │
                          │       ├─ get_shared_conversation   │
                          │       └─ _recipient_gate           │
                          │                                    │
                          │  reuse:                            │
                          │   • RuntimeServiceAuthenticator    │
                          │   • ConversationsService.get_*     │
                          │   • Existing message / event /     │
                          │     citation / draft / subagent    │
                          │     read paths.                    │
                          │   • WorkerAuditEmitter.emit_share* │
                          │   • NotificationDispatcher.        │
                          │     notify_share_created (when     │
                          │     adapter wired; no-op otherwise)│
                          └─────┬──────────────────┬───────────┘
                writes shares  │                  │ reads filtered snapshot
                                ▼                  ▼
                  ┌───────────────────────┐   ┌──────────────────────────────────┐
                  │ conversation_shares   │   │ agent_messages, runtime_events,  │
                  │ conversation_share_   │   │ runtime_citations,                │
                  │   recipients          │   │ runtime_drafts, runtime_subagent_│
                  │ (RLS, this PR)        │   │ results                           │
                  └───────────────────────┘   │ (existing tables, RLS)           │
                                              └──────────────────────────────────┘
```

The shape is the rule from PR 1.6: **one new service file, one new route file, one new migration. Re-use everything else.**

### 3.2 Streaming impact — explicitly **none** (with one nuance)

This is the question the user always flags ("how the entire system will come along — agent harness, streaming events, FE, DB schema").

| Subsystem                                  | Touched?                                                                                                                                   |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| `runtime_events` schema                    | **No.** No new `event_type`, no projection change.                                                                                         |
| `RuntimeEventEnvelope` Pydantic            | **No.**                                                                                                                                    |
| SSE handshake (`?after_sequence=N`)        | **No.** Recipients do not subscribe to SSE in v1.                                                                                          |
| Worker `runtime_worker/` job loop          | **No.** Sharing does not mint runs.                                                                                                        |
| Agent harness (LangGraph + DeepAgents)     | **No.** Sharing is a read filter; the agent never knows a share exists.                                                                    |
| Capabilities middleware, MCP loader, tools | **No.** Recipient view never invokes tools.                                                                                                |
| Citations / drafts / approvals / subagents | **No structural change.** Their _read endpoints_ gain a `?snapshot_at=` parameter (one keyword arg through the ports — typed and tested).  |
| Audit chain                                | **Additive.** Six new `action` constants. No chain semantic change.                                                                        |
| Notification dispatcher                    | **Additive.** One new method on the `NotificationDispatcher` Protocol; no-op default impl is the existing `LoggingNotificationDispatcher`. |

**The nuance:** the recipient endpoint reads `runtime_events`. The current event-listing path in [`agent_runtime/api/service.py`](../../services/ai-backend/src/agent_runtime/api/service.py) does not accept a `max_created_at` clamp. We add one keyword argument with a sensible default (`None` = no clamp) and route the recipient endpoint through it. This is a five-line change in the persistence port + adapter, exercised by an existing test that we extend.

### 3.3 Where sharing **logic** lives — and why

Per the service-boundaries doc: "tenants, IdP integration, permissions, product persistence, admin workflows" belong in _backend_. So why does `conversation_shares` live in _ai-backend_?

Because the _consumers_ of the row are the runtime services (read messages / events / citations / drafts / subagents), all of which already live in ai-backend with RLS keyed on `app.current_org_id`. A backend-owned `conversation_shares` table would force the recipient endpoint to chain backend → ai-backend HTTP per request, doubling latency for a strictly read-side feature, and would force backend to learn the runtime read schema (it doesn't and shouldn't).

`workspace_defaults` (PR 1.6) made the same call for the same reason. We follow the precedent.

The boundary that **does** matter — the `created_by_user_id` and `recipient_user_ids` are **opaque** to ai-backend. We rely on the facade (`FacadeAuthenticator`) to validate that a header `x-enterprise-user-id` is genuinely the caller, and on the existing `WorkspaceMembershipResolver` ([`agent_runtime/api/membership.py`](../../services/ai-backend/src/agent_runtime/api/membership.py), used by PR 1.4 forwarding) to confirm "user X is in org Y". Recipient validation calls the same resolver.

### 3.4 The recipient endpoint — request flow

```
recipient                FE                  facade               ai-backend                      Postgres
   │                      │                     │                     │                               │
   │  click share link    │                     │                     │                               │
   │ ─────────────────►   │                     │                     │                               │
   │                      │ React router →      │                     │                               │
   │                      │  /share/:token      │                     │                               │
   │                      │ ShareScreen mount   │                     │                               │
   │                      │                     │                     │                               │
   │                      │ GET /v1/agent/shares/{token}              │                               │
   │                      │ ──────────────────►│ /internal/v1/agent/shares/{token}                  │
   │                      │                     │ ──────────────────►│  ShareService.get_shared_     │
   │                      │                     │                     │   conversation(token, who)   │
   │                      │                     │                     │                               │
   │                      │                     │                     │  1) hash = sha256(token)      │
   │                      │                     │                     │  2) SELECT share row WHERE   │
   │                      │                     │                     │       share_token_hash=hash, │
   │                      │                     │                     │       revoked_at IS NULL,    │
   │                      │                     │                     │       (expires_at IS NULL OR │
   │                      │                     │                     │         now() < expires_at)  │
   │                      │                     │                     │     ──── token lookup is    │
   │                      │                     │                     │           ORG-AGNOSTIC      │
   │                      │                     │                     │                               │
   │                      │                     │                     │  3) if share.org_id ≠ who.org│
   │                      │                     │                     │       audit view_denied      │
   │                      │                     │                     │       → 404                  │
   │                      │                     │                     │                               │
   │                      │                     │                     │  4) recipient_gate(share,who)│
   │                      │                     │                     │     • workspace → org match  │
   │                      │                     │                     │     • specific → row in      │
   │                      │                     │                     │        conversation_share_   │
   │                      │                     │                     │        recipients            │
   │                      │                     │                     │     fail → audit + 403       │
   │                      │                     │                     │                               │
   │                      │                     │                     │  5) SET LOCAL                │
   │                      │                     │                     │       app.current_org_id =  │
   │                      │                     │                     │       share.org_id           │
   │                      │                     │                     │     (RLS now matches the     │
   │                      │                     │                     │      share's tenant)         │
   │                      │                     │                     │                               │
   │                      │                     │                     │  6) parallel reads:          │
   │                      │                     │                     │     • messages WHERE         │
   │                      │                     │                     │       conv = X AND created   │
   │                      │                     │                     │       <= snapshot_at         │
   │                      │                     │                     │     • events same clamp      │
   │                      │                     │                     │     • citations same clamp   │
   │                      │                     │                     │     • drafts latest version  │
   │                      │                     │                     │     • subagents              │
   │                      │                     │                     │                               │
   │                      │                     │                     │  7) if not sources_visible:  │
   │                      │                     │                     │     redact snippet/url/title │
   │                      │                     │                     │     in citations + events    │
   │                      │                     │                     │                               │
   │                      │                     │                     │  8) audit                    │
   │                      │                     │                     │      conversation.share.viewed│
   │                      │                     │                     │      (rate-limited 60s)      │
   │                      │                     │                     │                               │
   │                      │ ◄────────────────────────────────────────│  SharedConversationView     │
   │                      │ ReadOnlyThread renders                     │                               │
   │ ◄─── page paints     │                     │                     │                               │
```

Two subtle things in the diagram:

1. **Step 5 — `SET LOCAL`** is what makes RLS _work_ on the recipient request. The session GUC is set to the share's `org_id`, not the caller's. This is safe because step 3 already verified that the caller belongs to that org. Without this step, the message reads in step 6 would return zero rows (RLS would compare against the _caller_'s org, which we never set).
2. **Step 8 — rate-limited audit** uses the dedupe key `(share_id, viewer_user_id, ts // 60)`. If the same user refreshes 30 times in a minute we still emit one audit row. The dedupe is in-memory in the API service (a tiny LRU `dict[str, datetime]`); the table is the source of truth, not the cache.

### 3.5 The Share popover — creator UX

```
Sarah's chat → topbar Share button (PR 4.5) → SharePopover (this PR fills the slot)
┌──────────────────────────────────────────────────────────────────────┐
│  Share "FY26 Q1 launch announcement draft"                       ⓧ  │
├──────────────────────────────────────────────────────────────────────┤
│  Who can view                                                        │
│    ◉ Anyone in workspace                                             │
│    ◯ Specific people  [+ Add by name…]                               │
│                                                                      │
│  Sources visible to viewer        [ ON  ●○ ]                         │
│    Off → citations show "Source restricted"                          │
│                                                                      │
│  Link expiry                       [ Never ▾ ]                       │
│                                                                      │
│  ┌────────────────────────────────────────┐  [ Copy link ]   ─┐      │
│  │  https://atlas.acme.com/share/s_3f7… │                   │      │
│  └────────────────────────────────────────┘                   │      │
│                                                               │      │
│  Active shares on this chat                                   │      │
│    s_3f7b2c9 · workspace · created just now      [ Revoke ]   │      │
│                                                                      │
│                                          [ Done ]   [ Create new ]   │
└──────────────────────────────────────────────────────────────────────┘
```

Built from `@enterprise-search/design-system` primitives: `Popover`, `RadioGroup`, `Switch`, `TextInput` (for "Add by name" autocomplete reusing the existing `useWorkspaceMembers()` hook PR 4.2 introduces), `Button`, `Menu`. **Zero new design-system primitives.** The "Add by name" autocomplete reuses the same `MemberAutocomplete` component PR 1.4.1 introduces for approval forwarding — same backend, same cache.

The plaintext token surfaces in the URL field exactly once, on first paint after `createShare()`. Subsequent re-paints, browser back/forward, or component remount with the same `share_id` show only `s_3f7b2c9 · workspace · created just now` — the token is gone from FE state because it's never persisted to a store.

### 3.6 The Recipient view — read-only thread

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Atlas                              Shared by Sarah Chen · read-only    │
│                                          ┌───────────────────┐          │
│                                          │ Open in your chat │ (PR 6.2) │
│                                          └───────────────────┘          │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│   You · 11:42                                                           │
│   Draft the FY26 Q1 launch announcement using the approved positioning. │
│                                                                         │
│   Atlas · 11:42                                                         │
│   ▾ Read 3 docs in Notion · 1 deck in Drive · 4 Slack threads          │
│                                                                         │
│   Aurora 4.0 brings agentic search to every desk, not just engineering. │
│   Our hero claim emphasises time-to-answer and trust through            │
│   citations.[c1][c2]   ← citation chips render restricted vs full       │
│                                                                         │
│   Press window opens Apr 21, embargoes lift 9 AM ET.[c3]                │
│                                                                         │
│   ────────────────────────────────────────────────────────────────────  │
│   Sources                                                               │
│   [c1] Source restricted                          ← snippet hidden      │
│   [c2] Source restricted                                                │
│   [c3] Source restricted                                                │
└─────────────────────────────────────────────────────────────────────────┘
```

The whole screen is composed of components we already build:

- `ThreadBody` (PR 2.3) — read-only mode disables composer mount.
- `AssistantMessage`, `UserMessage` — unchanged.
- `ActivityCollapsible` — unchanged.
- `CitationChip` (PR 3.1) — gains a `restricted: boolean` prop; restricted chips render the chip with no tooltip body and route the click to a "you don't have access to this source" toast.
- `SourcesTab` (PR 3.2) — when `sources_visible_to_viewer === false`, render the rows with `Source restricted` placeholder copy. Same `<SourceRow>` component, two display states.
- `WorkspacePane` (PR 3.2) — recipient view reuses it but disables the Approvals tab (no decisions to take from a recipient view) and the Skills tab (recipient has no composer).

The "Open in your chat" button is a placeholder in this PR (disabled with a tooltip "Available in PR 6.2"). PR 6.2 wires it to `forkShare(token)`.

### 3.7 DRY — what we reuse vs. what we add

| Concern                             | Reuse                                                                                                                                              | Add                                                                                              |
| ----------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| Identity / RBAC                     | `RuntimeServiceAuthenticator` ([`runtime_api/auth.py:20-105`](../../services/ai-backend/src/runtime_api/auth.py)), `ADMIN_USERS` scope             | —                                                                                                |
| Workspace membership resolution     | `WorkspaceMembershipResolver` ([`agent_runtime/api/membership.py`](../../services/ai-backend/src/agent_runtime/api/membership.py))                 | one new method `is_member(org_id, user_id)` if not already present (likely is)                   |
| Token hashing                       | sha256 stored as hex (SCIM precedent)                                                                                                              | one ~40 LOC `share_token.py` module with `ShareTokenSecret` redacting wrapper                    |
| Persistence pool / migration runner | `agent_runtime/persistence/schema/migrate.py`                                                                                                      | one new migration                                                                                |
| Audit chain                         | `WorkerAuditEmitter` ([`runtime_worker/audit.py`](../../services/ai-backend/src/runtime_worker/audit.py))                                          | six new `action` constants + one helper `emit_share_event(action, metadata)`                     |
| RLS                                 | `tenant_isolation` policy pattern (migration 0008)                                                                                                 | two `CREATE POLICY` statements (one per new table)                                               |
| Notification port                   | `NotificationDispatcher` Protocol ([`agent_runtime/api/notifications.py:43-67`](../../services/ai-backend/src/agent_runtime/api/notifications.py)) | one new method `notify_share_created(share, recipient_user_ids)`                                 |
| FE design-system primitives         | `Popover`, `RadioGroup`, `Switch`, `TextInput`, `Button`, `Menu`, `Toast`                                                                          | —                                                                                                |
| FE shared chat surface              | `ThreadBody`, `AssistantMessage`, `UserMessage`, `ActivityCollapsible`, `CitationChip`, `SourcesTab`, `WorkspacePane`                              | one prop on `CitationChip`: `restricted: boolean`. One prop on `ThreadBody`: `readonly: boolean` |
| FE member autocomplete              | `MemberAutocomplete` (PR 1.4.1)                                                                                                                    | —                                                                                                |
| FE state                            | `useConversations()`, `useApi()`                                                                                                                   | one `useShares(conversationId)` and one `useSharedConversation(token)` hook                      |
| Facade proxy                        | `_forward()` pattern ([`workspace_routes.py:133-180`](../../services/backend-facade/src/backend_facade/workspace_routes.py))                       | one new `share_routes.py` registering five proxy routes                                          |
| Routing on the FE                   | React Router (already present)                                                                                                                     | one new route `/share/:token`                                                                    |

**Net new code** — target:

- 1 SQL migration (~50 lines).
- 1 module `share_token.py` (~40 LOC).
- 1 service file `share_service.py` (~180 LOC including recipient-view orchestration).
- 1 route file `http/share_routes.py` (~90 LOC).
- 1 store file `share_store.py` × 2 (in-memory + Postgres adapter, ~120 LOC each).
- 1 facade route file `share_routes.py` (~60 LOC).
- 1 FE feature folder `features/share/` (~350 LOC across SharePopover, ShareScreen, hooks, tests).
- 1 contract addition in `api-types/index.ts` (~50 LOC).

Total target: **~1,000 LOC** including ~250 LOC of test fixtures + table-driven validators.

### 3.8 No third-party middleware needed

| Candidate                                                    | Why we skip                                                                                                                                                                                                          |
| ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `python-itsdangerous` / `jose` / signed-token libraries      | Bearer tokens we hash. We don't sign claims — there are no claims; the row is the source of truth. Itsdangerous would invert the architecture (token carries data) and force us to handle key rotation from scratch. |
| `python-shareable-links` (no real library, but worth naming) | The pattern is two SQL columns + a hash function. A library would be net-negative.                                                                                                                                   |
| `python-rate-limit` / `slowapi`                              | The `view` audit dedupe is a 1-minute LRU on `(share_id, user_id)`. ~12 LOC. A library brings a Redis dep and global config we don't need.                                                                           |
| `pydantic-encryption` / per-row envelopes                    | We deliberately don't encrypt the share row's content (it carries no PII — just IDs, hash, flags). The conversation content remains encrypted by `FieldEncryption` in the rows it always lived in.                   |
| RBAC middleware (Casbin / OPA)                               | One new authz check ("is recipient on this share's allow-list") implemented in 12 lines of Python. Adding a policy engine for one rule is overengineering.                                                           |
| Public-link middleware (e.g., `fastapi-share-link`)          | The token-gated route is a regular FastAPI route; the auth dependency just additionally requires a session. No middleware needed.                                                                                    |
| FE: any sharing widget library                               | The popover composes existing design-system primitives. A widget library would force a parallel design language.                                                                                                     |

The only library decision worth flagging is **how the FE constructs the share URL**. We use a single deployment-config field `app_base_url` ([`backend_facade/deployment_profile.py`](../../services/backend-facade/src/backend_facade/deployment_profile.py)) plus the path `/share/{token}`. No URL builder library; no signed-URL service. The token is the unguessable secret.

### 3.9 Edge cases

| Case                                                                                                          | Behaviour                                                                                                                                                                                                                                                        |
| ------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Recipient opens a share whose source conversation was soft-deleted after share creation                       | 404. Audit `share.view_denied` with reason `conversation_deleted`. Documented in copy ("This shared chat is no longer available").                                                                                                                               |
| Conversation is restored after soft-delete; same share token                                                  | 200 again — token is unchanged, `revoked_at` was never set on the share, conversation rows return again.                                                                                                                                                         |
| Source thread had a still-running run when share was created                                                  | Snapshot is `now()` at create time. Recipient sees up to that `sequence_no`. New events past that are filtered server-side.                                                                                                                                      |
| `view_access="specific"` with a recipient who's also a workspace admin                                        | They access through the recipient gate (`row in recipients` matches first). No double-audit.                                                                                                                                                                     |
| Two concurrent `POST /share` calls with `include_link=true`                                                   | Two share rows, two distinct tokens. Independent. No global lock.                                                                                                                                                                                                |
| `PATCH /shares/{id}` removes a recipient who is currently viewing                                             | Recipient's next refresh returns 403. They can still re-open via the link if `view_access="workspace"`; not if `"specific"`.                                                                                                                                     |
| Token entropy collision (sha256 prefix collides across two shares)                                            | `share_token_hash` is the full 64-hex digest; `share_token_prefix` is UI-only, not used for lookup. Collision probability: cryptographic.                                                                                                                        |
| Share row exists but conversation row's `org_id` was changed (impossible today; defensive)                    | Cross-org refusal triggers; share stays unreadable. We do not chase data integrity in the read path.                                                                                                                                                             |
| User signs out while viewing — session expires                                                                | Existing session-expiry flow takes over (the `/share/:token` route is auth-gated like the rest of the app). After re-login, recipient lands back on the share via the existing post-login redirect.                                                              |
| Link is shared to a logged-in user from a different workspace                                                 | They land on `/share/:token`, get 404 (the cross-org refusal). Copy: "This share isn't available to your workspace." (Copy is the only way they distinguish from "doesn't exist" — by design; we don't reveal cross-tenant data.)                                |
| Ten thousand recipients on one `specific` share                                                               | The recipients table is keyed `(share_id, user_id)`; PG handles it. We cap at 200 in the API for UX guidance (one `recipient_count_exceeded` error if exceeded), revisit when product asks.                                                                      |
| Share + connector revoke race (creator revokes Slack OAuth after share with `sources_visible_to_viewer=true`) | The recipient still sees what was _captured at snapshot time_ — the existing citation snippet in `runtime_citations`. Live re-fetch of source content is not part of the recipient view. (Per design: "Atlas does not exfiltrate" — this is captured, not live.) |

### 3.10 Test plan (lives in this PR)

**ai-backend (`services/ai-backend/tests/`)**

- `unit/runtime_api/share/test_create_share.py`
  - happy paths: workspace-no-link, workspace-with-link, specific-with-2-recipients, all combinations of `sources_visible_to_viewer` + `expires_at`
  - non-owner non-admin → 403, no rows
  - empty recipients on specific → 422
  - recipient outside org → 422
- `unit/runtime_api/share/test_recipient_view.py`
  - workspace gate: same-org member success, cross-org 404
  - specific gate: listed user 200, unlisted user 403
  - snapshot filtering: messages newer than snapshot omitted; events newer omitted; drafts latest-version respected
  - `sources_visible_to_viewer` redaction: every snippet/url/title null when off; passthrough when on
  - revoked share → 404; expired share → 404
- `unit/runtime_api/share/test_revoke.py` — soft-revoke; subsequent view 404; audit emitted
- `unit/runtime_api/share/test_audit_emission.py` — exactly one row per privileged action, dedupe keys correct on view
- `unit/agent_runtime/api/share_token/test_share_token.py` — wrapper redaction (repr/str), prefix shape, hash determinism
- `integration/test_share_token_leakage.py` — capture all logs from a full create/view cycle; assert plaintext token absent
- `integration/test_share_audit_chain.py` — chain verifier passes after a six-action sequence

**Frontend (`apps/frontend/src/`)**

- `features/share/SharePopover.test.tsx` — radio + recipient autocomplete + create flow; one-time token surfacing; revoke updates list
- `features/share/ShareScreen.test.tsx` — read-only mode (no composer mount, no model picker, fork button disabled); restricted citations render placeholder + tooltip
- `features/share/useSharedConversation.test.tsx` — 404 → "share unavailable" copy; 403 → "not for you" copy
- `api/agentApi.test.ts` extended — five new functions hit the right paths with the right shapes

**Cross-service smoke (`make test`)** — one happy path: create → list → recipient view → revoke → recipient view fails.

### 3.11 Rollout

- **Flag-free.** New tables start empty; the `ShareButton` popover is gated by the existence of `agentApi.createShare` (it always exists post-merge; no flag).
- **Zero-downtime migrations.** `CREATE TABLE IF NOT EXISTS`; new index is `CREATE UNIQUE INDEX IF NOT EXISTS` (operator runbook addendum: run with `CONCURRENTLY` in production). No `ALTER` on existing tables.
- **Backout.** Drop both new tables; the FE's Share popover surfaces a degraded state ("Sharing temporarily unavailable") via the existing API-error toast; chat surface unaffected.
- **Forward compatibility for PR 6.2.** PR 6.2 adds (a) FK self-reference on `agent_conversations.parent_conversation_id` (declared by PR 1.6, no FK yet), and (b) one new column `forked_from_share_id` on the same table — both additive, no `conversation_shares` change.
- **Feature flag escape hatch (optional, off by default).** A `RUNTIME_SHARING_ENABLED=true|false` env on ai-backend can short-circuit the routes with 503 if a deployment chooses to delay sharing for a particular tenant. Not on by default; documented in the deployment runbook for use in regulated-buyer environments where conversation sharing must be reviewed first.

### 3.12 Open questions

1. **Link expiry default.** v1: `null` (never expires) unless caller specifies. Some buyers require a default expiry. Not blocking; add `RUNTIME_SHARING_DEFAULT_EXPIRY_DAYS` env later if asked.
2. **Per-source ACL.** Some recipients can see Slack but not Drive. v1 says global flag only. Tracked as PR 6.1.1 once we have a per-user-per-connector membership lookup that doesn't pivot through OAuth state.
3. **Share digest in notifications.** When the notification matrix (PR 4.1) is wired, do shares trigger Slack DM by default? Default off, opt-in per recipient. Captured in PR 4.1 follow-up.

---

## 4 · Acceptance checklist

- [ ] Migration `0022_conversation_shares.sql` applies cleanly forward and rolls back; both tables have `tenant_isolation` policies; partial unique index is in place.
- [ ] `share_token.py` mints, hashes, and redacts. `ShareTokenSecret(...).__repr__()` yields the redacted form.
- [ ] `ShareService.create_share` writes the share row + (optional) recipient rows + audit row in a single TX. Plaintext token returned exactly once.
- [ ] `ShareService.get_shared_conversation` enforces cross-org refusal _before_ org-switching the GUC. Recipient gate enforced for both modes. Snapshot clamp applied to messages, events, citations, drafts, subagents.
- [ ] `ShareService.revoke_share` is idempotent; subsequent calls 404; audit row emitted on first call only.
- [ ] `ShareService.update_share` accepts merge-patch; recipients diff yields `recipient_added`/`recipient_removed` audit rows.
- [ ] No new `event_type` in `runtime_api/schemas/events.py`. `RuntimeEventEnvelope` Pydantic schema byte-identical pre/post merge.
- [ ] `backend-facade/share_routes.py` proxies five public routes (`POST/GET /conversations/{id}/share[s]`, `PATCH/DELETE /shares/{id}`, `GET /shares/{token}`); none reach `/internal/v1/*`.
- [ ] `@enterprise-search/api-types` exports `ConversationShare`, `CreateShareRequest`, `CreateShareResponse`, `UpdateShareRequest`, `SharedConversationView`, `ShareViewAccess`.
- [ ] `apps/frontend/src/features/share/` ships `SharePopover.tsx`, `ShareScreen.tsx`, `useShares.ts`, `useSharedConversation.ts` with tests.
- [ ] `CitationChip` extended with `restricted: boolean` (no breaking change). `SourcesTab` renders restricted state.
- [ ] React Router has `/share/:token`; outside the auth-bypass layer.
- [ ] Audit verifier passes after a 6-action sequence (`created`, `viewed`, `recipient_added`, `recipient_removed`, `updated`, `revoked`).
- [ ] `make test` green; ai-backend full suite green; frontend typecheck + build green.

---

## 5 · References

- Atlas Design Doc handoff bundle, §"Flow — Share" (recipient view + fork mechanics) and §"Decisions log" ("Atlas does not exfiltrate facts").
- [`docs/architecture/runtime-stream-handshake.md`](../architecture/runtime-stream-handshake.md) — unchanged by this PR; explicit non-event.
- [`docs/architecture/service-boundaries.md`](../architecture/service-boundaries.md) — facade-only ingress; ai-backend owns runtime-side persistence.
- [`docs/decomp/persistence/_index.md`](../decomp/persistence/_index.md) — persistence inventory.
- [`services/ai-backend/migrations/0003_audit_hardening.sql`](../../services/ai-backend/migrations/0003_audit_hardening.sql) — runtime audit chain reused.
- [`services/ai-backend/migrations/0008_rls_tenant_isolation.sql`](../../services/ai-backend/migrations/0008_rls_tenant_isolation.sql) — RLS pattern reused.
- [`services/backend/migrations/0015_scim.sql`](../../services/backend/migrations/0015_scim.sql) — SCIM token hashing pattern adopted verbatim.
- [`services/backend/src/backend_app/identity/scim_store.py`](../../services/backend/src/backend_app/identity/scim_store.py) — SCIM token store for reference.
- [`services/ai-backend/src/runtime_api/auth.py`](../../services/ai-backend/src/runtime_api/auth.py) `RuntimeServiceAuthenticator` — identity parsing reused.
- [`services/ai-backend/src/agent_runtime/api/membership.py`](../../services/ai-backend/src/agent_runtime/api/membership.py) `WorkspaceMembershipResolver` — reused for recipient validation.
- [`services/ai-backend/src/agent_runtime/api/notifications.py`](../../services/ai-backend/src/agent_runtime/api/notifications.py) `NotificationDispatcher` — extended with one method.
- [`services/ai-backend/src/runtime_worker/audit.py`](../../services/ai-backend/src/runtime_worker/audit.py) `WorkerAuditEmitter` — extended with six action constants.
- [`services/backend-facade/src/backend_facade/workspace_routes.py`](../../services/backend-facade/src/backend_facade/workspace_routes.py) `_forward` — proxy pattern reused.
- [`packages/service-contracts/src/enterprise_service_contracts/headers.py`](../../packages/service-contracts/src/enterprise_service_contracts/headers.py) — header constants reused.
- [`apps/frontend/src/api/agentApi.ts`](../../apps/frontend/src/api/agentApi.ts) — five new client functions added.
- [`docs/new-design/pr-1.6-workspace-defaults-conversation-lifecycle.md`](pr-1.6-workspace-defaults-conversation-lifecycle.md) — sibling PR; provides `parent_conversation_id` forward-declaration we depend on.
- [`docs/new-design/pr-3.1-citation-chips-sources-tab.md`](pr-3.1-citation-chips-sources-tab.md) · [`pr-3.2-workspace-pane-right-rail.md`](pr-3.2-workspace-pane-right-rail.md) — provide `CitationChip`, `SourcesTab`, `WorkspacePane` we extend.
- [`docs/new-design/pr-4.5-usage-overlay-share-popover.md`](pr-4.5-usage-overlay-share-popover.md) — landed the `ShareButton` placeholder this PR fills.
- [`docs/new-design/pr-1.4.1-approval-forwarding-hardening.md`](pr-1.4.1-approval-forwarding-hardening.md) — provides `MemberAutocomplete` and the membership lookup pattern we reuse.
- RFC 7396 — JSON Merge Patch (semantics adopted for `PATCH /shares/{id}`; no library).
- [FastAPI · Body — Updates](https://fastapi.tiangolo.com/tutorial/body-updates/) — `model_dump(exclude_unset=True)` pattern for PATCH.

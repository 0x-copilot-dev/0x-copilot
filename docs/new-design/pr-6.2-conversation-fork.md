# PR 6.2 — Conversation fork mechanic

> **Status:** Draft · PRD + Spec + Architecture
> **Plan reference:** Wave 6, PR 6.2 in [`/Users/parthpahwa/.claude/plans/fetch-this-design-file-resilient-pumpkin.md`](../../../.claude/plans/fetch-this-design-file-resilient-pumpkin.md)
> **Owner:** ai-backend (write path) · backend-facade (proxy) · frontend ("Open in your chat" button on recipient view)
> **Size:** M · One ALTER + one new column + one index, one new endpoint, one FE call site. ~350 net LOC including tests.
> **Reads alongside:** [`docs/architecture/runtime-stream-handshake.md`](../architecture/runtime-stream-handshake.md), [`docs/architecture/service-boundaries.md`](../architecture/service-boundaries.md).
> **Sibling docs (sequencing):**
> – [PR 1.6 — Workspace defaults + conversation lifecycle](pr-1.6-workspace-defaults-conversation-lifecycle.md) (forward-declared `parent_conversation_id` column on `agent_conversations`; this PR adds the FK and index)
> – [PR 6.1 — Conversation sharing schema + create flow](pr-6.1-conversation-sharing.md) (provides the share row + recipient view this PR forks from)
> – [PR 1.2 — Per-chat connector scope](pr-1-2-per-chat-connector-scope.md) (the recipient's connector defaults flow into the new conversation through this scope)

---

## 1 · PRD

### 1.1 Problem

The Atlas design doc (§Flow — Share, step 3) requires that a recipient of a shared conversation can **continue the conversation in their own chat** — not by editing the shared (read-only) thread, but by **forking** it: a new conversation owned by the recipient, that starts with the historical turns visible, and uses the recipient's connector set going forward.

> _"The recipient can fork the thread to continue with their own access. Forking creates a new chat that starts where the share left off but uses the recipient's connector set going forward."_ — Atlas Design Doc, Flow — Share

PR 6.1 ships the read-only recipient view but the "Open in your chat" button is a stub. This PR wires it.

The fork is **not** a clever pointer-based view: a forked conversation is a real, owned, mutable conversation row in the recipient's account, populated with copies of the source turns up to the share's `snapshot_at`. The recipient can edit / regenerate / branch / delete those copies as if they had typed them — they are now their own data.

### 1.2 Goals

1. **One endpoint, one transaction.** `POST /v1/agent/shares/{share_token}/fork` resolves the share, validates the recipient, creates the new conversation, copies the snapshot of `agent_messages`, audits, returns the new `conversation_id`. All in one TX.
2. **Forked conversations are first-class.** They appear in the recipient's sidebar under "Today", they accept new prompts immediately, they pass through the existing `RunService.create_run` path without a special branch, and they participate in connector scope / workspace defaults / retention exactly like ⌘N-created conversations.
3. **Lineage is queryable.** `agent_conversations.parent_conversation_id` (forward-declared by PR 1.6) gains its FK self-reference + index. A new column `forked_from_share_id` records the share that authorised the fork (audit-grade, non-FK so revoking the share doesn't break the conversation).
4. **Streaming, agent harness, capabilities middleware are untouched.** No new event type. No new tool. The harness has no idea the recipient's chat is a fork — it sees a new conversation with seeded messages, same as if the user had pasted them in. (Actually slightly cleaner: the seeded messages have `run_id = NULL`, so the harness skips them when computing a run's prior context.)
5. **DRY.** Reuse `RunService` / `ConversationsService` paths, `WorkspaceMembershipResolver`, `WorkerAuditEmitter`, `FieldEncryption`, the existing message persistence port. One new service method. One new column. One new index. One new endpoint.

### 1.3 Non-goals (this PR)

- **Live following / shadowing.** A fork is a snapshot copy. If the source conversation continues, the fork does not pick up new turns. (To "re-sync", the recipient would re-fork from a newer share.)
- **Citation chip continuity in copied turns.** The historical messages contain `[c<id>]` tokens that reference `runtime_citations` rows tied to the source conversation. We do **not** copy those citation rows in v1; the FE renders unknown citation IDs as muted text (the same fallback PR 3.1 already needs for out-of-order streaming). Per-source-citation copy is captured as follow-up PR 6.2.1.
- **Cross-org forks.** A recipient's `org_id` must equal the share's `org_id`. Cross-org delegation needs a trust contract that doesn't exist today.
- **Forking forks.** A forked conversation can itself be shared (PR 6.1) and therefore re-forked, but the lineage is captured as a one-step-back `parent_conversation_id`. We do not maintain multi-hop chains in v1; the audit chain captures the full history if a forensic reader needs it.
- **Activity / event copy.** `runtime_events` rows are not copied to the fork. The historical assistant turns appear with their content and "Sources strip" inline (rendered from message metadata, not events). Activity timelines for prior turns are not navigable from the fork — they remain available in the source conversation if the user goes back to the share.
- **Drafts copy.** A `runtime_drafts` row tied to the source conversation is not copied. Drafts are mutable, post-stream artefacts; carrying half-edited drafts into a fork creates ownership ambiguity. The recipient can regenerate the draft in their forked chat with one prompt.
- **Subagent-result copy.** Same rationale.
- **Branching the _source_ thread instead of forking.** Forking creates a new owned chat; branching is a different mechanism (P1 in the Atlas TODOs). They share zero schema; this PR does not preempt branching.

### 1.4 Acceptance criteria

| #     | Criterion                                                                                                                                                                                                                                               | Verified by                                 |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| AC-1  | `POST /v1/agent/shares/{share_token}/fork` returns `{conversation_id, fork_message_count}` for a valid share where the calling identity passes the recipient gate.                                                                                      | Unit test on `ConversationForkService.fork` |
| AC-2  | Cross-org caller → 404 (no leak). Foreign-recipient on `view_access="specific"` → 403. Revoked / expired share → 404. Same error semantics as the recipient view in PR 6.1.                                                                             | Permission matrix test                      |
| AC-3  | The new `agent_conversations` row has `parent_conversation_id = source.id`, `forked_from_share_id = share.share_id`, `user_id = recipient.user_id`, `org_id = source.org_id`, `enabled_connectors = {}` (so workspace_defaults applies on next run).    | Persistence test                            |
| AC-4  | `agent_messages` rows up to `snapshot_at` are copied with new IDs; `parent_message_id` is rewritten to point within the new conversation; `source_message_id`, `branch_id`, `run_id` are set to `NULL`. `deleted_at IS NOT NULL` rows are not copied.   | Snapshot-fidelity test                      |
| AC-5  | Encrypted message content is decrypted and re-encrypted into the new row through the existing `FieldEncryption` codec — the new envelope has its own IV, no DEK reuse across rows.                                                                      | Encryption round-trip test                  |
| AC-6  | Sending the first prompt in a forked conversation calls the existing `RunService.create_run` with no special path. The run's snapshot of `enabled_connectors` resolves through the standard PR 1.6 chain (request → conversation → workspace_defaults). | Integration test against `make dev`         |
| AC-7  | `conversation.fork` is the only new audit `action`. Metadata: `{ source_conversation_id, source_org_id, share_id, snapshot_at, message_count }`. Chain verifier passes.                                                                                 | Audit chain test                            |
| AC-8  | A fork of a conversation containing > `RUNTIME_FORK_MAX_MESSAGES` (default 500) is rejected with 422 `fork_too_large`. No partial fork written.                                                                                                         | Limit test                                  |
| AC-9  | After fork, deleting the source conversation does not delete the fork (FK is `ON DELETE SET NULL`); the fork's `parent_conversation_id` becomes `NULL` while `forked_from_share_id` retains the audit pointer.                                          | Cascade test                                |
| AC-10 | The streaming handshake is byte-identical pre/post merge. `RuntimeEventEnvelope` schema unchanged. PR 1.6 / 6.1 / 1.1 / 1.3 / 1.4 / 1.5 in flight produce no merge conflict.                                                                            | Schema regression test                      |
| AC-11 | The FE "Open in your chat" button on `ShareScreen.tsx` calls `forkShare(token)`, navigates to `/?conversationId={new_id}`, and the existing `ChatScreen` auto-loads the conversation.                                                                   | FE integration test                         |

### 1.5 User stories

| #    | Persona                                 | Story                                                                                                                                                                                                                                                                                                                                        |
| ---- | --------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| US-1 | Marcus (recipient on a workspace share) | I'm reading Sarah's shared "FY26 Q1 launch" thread. I have a follow-up question I want to explore against the GTM data _I_ can see (which Sarah might not, since I'm in the GTM Slack channel). I click **Open in your chat**. A new chat appears in my sidebar with the prior turns visible; I prompt; Atlas reads through _my_ connectors. |
| US-2 | Devi (specific-people recipient)        | Sarah forwarded a sensitive draft to Devi privately. Devi forks; Devi's fork has Devi as `created_by`, Devi's connector scopes apply going forward. Sarah's source thread is unchanged.                                                                                                                                                      |
| US-3 | A recipient who lacks fork rights       | A recipient on a _workspace_ share where the share was revoked between page-load and click → 404 with a clear toast ("This share has been revoked"). FE deletes the optimistic sidebar entry it tentatively created.                                                                                                                         |
| US-4 | Sarah (creator)                         | I see a notification or audit-row entry: "Marcus forked your shared chat at 11:46." (Notifications fan-out gated on PR 4.1 matrix; audit row guaranteed.)                                                                                                                                                                                    |
| US-5 | Sarah deletes the source after the fork | Marcus's fork is unaffected — his chat continues. The fork's `parent_conversation_id` becomes `NULL` (FK `ON DELETE SET NULL`), the `forked_from_share_id` persists for audit. The fork's title is unaffected ("Forked from FY26 Q1 launch announcement draft").                                                                             |
| US-6 | Auditor                                 | I export the audit log; one fork produces one `conversation.fork` row with `{source_conversation_id, share_id, message_count, snapshot_at}`, signed in the existing chain.                                                                                                                                                                   |
| US-7 | Marcus reaches the message limit        | Marcus tries to fork a 1,200-message thread. He gets 422 with copy "This chat is too long to open in your own chat. Continue from the source instead." The button stays clickable but grays after the first failure.                                                                                                                         |

### 1.6 Risks

| Risk                                                                                      | Mitigation                                                                                                                                                                                                                                                                                                                 |
| ----------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Large-thread fork blows the request budget                                                | Hard cap `RUNTIME_FORK_MAX_MESSAGES=500`. Reject with 422 `fork_too_large`. v1 ships the cap; relaxation requires a paginated background job (out of scope).                                                                                                                                                               |
| Encryption envelope copy reuses DEKs across rows                                          | We **decrypt + re-encrypt** through `FieldEncryption.encrypt(plaintext, table, column, org_id)` for every copied content field. No envelope memcpy. A test asserts that `new_row.envelope.iv != source_row.envelope.iv` for at least one column.                                                                           |
| `parent_message_id` rewrite is incorrect (depth-first traversal misses orphan branches)   | Source messages are copied in `created_at ASC` order; the `old → new` map is built incrementally; `parent_message_id` lookup at insert time is O(1). If a parent is missing in the source set (data integrity violation), we set `parent_message_id = NULL` and emit a warning (audit-tagged, not failed).                 |
| The harness later sees seeded messages and treats them as if they were generated by a run | Seeded messages have `run_id = NULL`. The existing context-builder in [`agent_runtime/context/`](../../services/ai-backend/src/agent_runtime/context/) already filters by `run_id IS NOT NULL` for run-scoped context; the historical messages are visible to the model only as conversation history, exactly as intended. |
| Citation chips in copied messages render as broken                                        | The FE markdown plugin (PR 3.1) already renders unknown `[c<id>]` tokens as muted text. We document this behaviour in copy ("Source links are available in the original chat"). PR 6.2.1 follow-up copies citation rows for chip continuity.                                                                               |
| Concurrent fork attempts produce duplicate conversations                                  | The endpoint is non-idempotent on purpose (each call mints a new conversation). The FE disables the button while the request is in flight; double-clicks beyond that produce two forks (acceptable; the user can delete one).                                                                                              |
| Fork while source conversation is mid-stream                                              | The snapshot is the share's `snapshot_at`, not "now". The source's running state is invisible to the fork operation. No race.                                                                                                                                                                                              |
| Fork triggers retention sweeper for messages older than `snapshot_at`                     | The retention sweeper (existing) keys on the _fork's_ `org_id` and the fork's TTL, not the source's. Copied messages get `created_at = NOW()` (the fork moment) so they're not immediately reapable; their original timestamps go into a `metadata_json.original_created_at` field for context.                            |
| FK `parent_conversation_id` self-reference creates a cyclic-delete pathology              | We use `ON DELETE SET NULL`, not `ON DELETE CASCADE`. A source delete severs the parent pointer; a fork delete is independent. There is no cycle (a forked conversation cannot be its own ancestor — the recipient's user_id breaks any cycle).                                                                            |
| Notification spam (creator gets pinged on every fork)                                     | Notification fan-out is gated by the Settings → Notifications matrix (PR 4.1) — opt-in per event-type. v1 default: `share.forked` notifications are _off by default_; the creator can opt in.                                                                                                                              |

### 1.7 Unit testing requirements

- `tests/unit/agent_runtime/persistence/test_message_copy.py` — `copy_messages_for_fork(source_conv_id, target_conv_id, snapshot_at, max_messages)`: fidelity, ID rewrite, NULL reset on `run_id`/`source_message_id`/`branch_id`, encryption round-trip, deleted-row exclusion, max-cap enforcement.
- `tests/unit/runtime_api/services/test_conversation_fork_service.py` — happy paths (workspace, specific), permission denials (cross-org, non-recipient, revoked, expired), max-cap, FK integrity after source delete, audit emission shape.
- `tests/unit/runtime_api/http/test_fork_route.py` — route shape, identity propagation, error mapping.
- `tests/integration/test_fork_then_run.py` — fork → send a prompt → SSE event stream completes (verifies the seeded messages don't break the harness).
- `tests/integration/test_fork_audit_chain.py` — chain verifier passes after a fork-then-run sequence.

Frontend tests:

- `apps/frontend/src/features/share/ShareScreen.test.tsx` (extended) — "Open in your chat" button enabled iff `share.view_access` allows fork (always, in v1) AND the user's session is valid; click → `forkShare(token)` → router push.
- `apps/frontend/src/api/agentApi.test.ts` (extended) — `forkShare` shape.

---

## 2 · Spec

### 2.1 Wire — one endpoint

| Verb   | Path                                  | Auth                                                                                                   | Effect                                                                                                               |
| ------ | ------------------------------------- | ------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------- |
| `POST` | `/v1/agent/shares/{share_token}/fork` | Identity (any logged-in user; same recipient gate as `GET /v1/agent/shares/{share_token}` from PR 6.1) | Resolve share → validate recipient → create new conversation owned by recipient → copy snapshot of messages → audit. |

#### 2.1.1 Request

```jsonc
{
  "title": "FY26 Q1 launch — my exploration", // optional; defaults to "Forked from {source.title}"
  "folder": "Launches", // optional
}
```

Both fields are optional and follow PR 1.6's PATCH-conversation semantics — they pass through to the new row's `title` / `folder` columns. If `title` is omitted we use `f"Forked from {source_title}"` (truncated to 240 chars).

#### 2.1.2 Response

```jsonc
{
  "conversation_id": "conv_01HZ…",
  "parent_conversation_id": "conv_…src",
  "forked_from_share_id": "share_…",
  "fork_message_count": 14,
  "title": "FY26 Q1 launch — my exploration",
  "folder": "Launches",
  "created_at": "2026-05-05T18:46:09.012Z",
  "user_id": "user_…recipient",
}
```

The shape is `ForkResponse`. The FE uses `conversation_id` to navigate; `fork_message_count` is for the post-fork toast ("Opened with 14 prior turns").

### 2.2 Persistence

#### 2.2.1 `migrations/0023_conversation_fork_lineage.sql`

```sql
-- This PR completes the fork lineage that PR 1.6 forward-declared.
-- PR 1.6 added the `parent_conversation_id` column on agent_conversations;
-- this PR adds the FK self-reference (ON DELETE SET NULL — never cascade)
-- and a sparse index. We also add the audit pointer column.
--
-- Why ON DELETE SET NULL:
--   • A user deletes the *source* chat → the fork stays (it's the recipient's
--     property; the recipient kept reading after the source was gone).
--   • The pointer becomes NULL but `forked_from_share_id` survives so the
--     audit chain still threads back to the original share row.
--
-- Why a separate `forked_from_share_id` column instead of just relying on
-- `parent_conversation_id`:
--   • A share can outlive the source conversation if the conversation is
--     soft-deleted (PR 1.6) and then retention-reaped after the share is
--     revoked. The audit pointer to *which share authorised the fork* is
--     useful even when the source row is gone.
--   • No FK on this column — share rows can be revoked/cleaned up
--     independently; the audit pointer is informational, not relational.

ALTER TABLE agent_conversations
  ADD COLUMN IF NOT EXISTS forked_from_share_id TEXT;

ALTER TABLE agent_conversations
  ADD CONSTRAINT fk_agent_conversations_parent
    FOREIGN KEY (parent_conversation_id)
    REFERENCES agent_conversations(id)
    ON DELETE SET NULL;

-- Sparse — only forks have a parent. Most conversations don't, so a partial
-- index keeps it tiny.
CREATE INDEX IF NOT EXISTS idx_agent_conversations_parent
  ON agent_conversations (parent_conversation_id)
  WHERE parent_conversation_id IS NOT NULL;
```

Rollback (`0023_conversation_fork_lineage.rollback.sql`) drops the constraint, drops the index, drops the column.

#### 2.2.2 What we are _not_ adding

| Thing                                                                                                   | Why not                                                                                                                                                                                                                                                                                          |
| ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| New table `conversation_forks`                                                                          | The fork is a conversation row with two pointers. A separate table would bring no query, no constraint, no policy gain.                                                                                                                                                                          |
| `fork_depth` / `fork_root_id` columns                                                                   | Multi-hop chain support. v1 doesn't need it; a recursive CTE on `parent_conversation_id` answers "ancestors of X" if a forensic query ever needs it.                                                                                                                                             |
| Copy `runtime_events` / `runtime_citations` / `runtime_drafts` / `runtime_subagent_results` to the fork | None of these have ownership ambiguity for the _new_ conversation. Drafts are post-stream artefacts; copying them invents two owners for one editable surface. Citations / events tie to runs that didn't happen in the fork. v1 keeps the fork's run history empty until the recipient prompts. |
| Trigger to enforce `parent_conversation_id` ↔ `forked_from_share_id` consistency                        | We accept that the share could be revoked between a fork being persisted and a forensic read. The ALTER constraint would block legitimate cleanup. The audit row (`conversation.fork`) is the immutable source of truth for "did this fork happen".                                              |

### 2.3 The fork operation — one transaction

```python
# services/ai-backend/src/runtime_api/services/conversation_fork.py  (new, ~110 LOC)

class ConversationForkService:
    def __init__(
        self,
        persistence: AsyncPersistencePort,
        membership: WorkspaceMembershipResolver,
        audit: WorkerAuditEmitter,
        encryption: FieldEncryption,
        clock: Clock = system_clock,
        max_messages: int = settings.RUNTIME_FORK_MAX_MESSAGES,
    ) -> None:
        ...

    async def fork(
        self,
        *,
        share_token: str,
        identity: TrustedRequestIdentity,
        title: str | None = None,
        folder: str | None = None,
    ) -> ForkResponse:
        """
        Atomically:
          1. Resolve the share (org-agnostic by token hash).
          2. Validate the recipient (same gate as GET /shares/{token} from PR 6.1).
          3. Create the new agent_conversations row owned by the recipient.
          4. Copy messages (snapshot_at-clamped) with re-encryption + ID rewrite.
          5. Append one runtime_audit_log row.
          6. Notify share creator (best-effort, fire-and-forget).
        """
        share = await self._resolve_share_or_404(share_token)
        await self._recipient_gate_or_403(share, identity)            # reuse PR 6.1 helper

        async with self.persistence.transaction(org_id=share.org_id):
            source = await self.persistence.get_conversation(
                org_id=share.org_id, conversation_id=share.conversation_id,
            )
            if source is None or source.deleted_at is not None:
                raise ShareError.SHARE_NOT_FOUND   # 404

            messages = await self.persistence.list_messages(
                org_id=share.org_id,
                conversation_id=share.conversation_id,
                created_at_max=share.snapshot_at,
                include_deleted=False,
                limit=self.max_messages + 1,    # +1 to detect overflow
            )
            if len(messages) > self.max_messages:
                raise ForkError.FORK_TOO_LARGE  # 422

            new_conv = ConversationRecord(
                id=new_conversation_id(),
                org_id=share.org_id,
                user_id=identity.user_id,                          # recipient owns
                assistant_id=source.assistant_id,
                title=title or self._derived_title(source.title),
                folder=folder,
                metadata={"forked_from_share_id": share.share_id},
                enabled_connectors={},                             # workspace_defaults applies on next run
                parent_conversation_id=source.id,
                forked_from_share_id=share.share_id,
                created_at=self.clock.now(),
                updated_at=self.clock.now(),
            )
            await self.persistence.create_conversation(new_conv)

            new_message_count = await self._copy_messages(
                source_messages=messages,
                target_conv=new_conv,
                actor_user_id=identity.user_id,
            )

            await self.audit.emit_conversation_fork(
                org_id=share.org_id,
                actor_user_id=identity.user_id,
                source_conversation_id=share.conversation_id,
                target_conversation_id=new_conv.id,
                share_id=share.share_id,
                snapshot_at=share.snapshot_at,
                message_count=new_message_count,
            )

        # outside the TX so a notification failure doesn't abort the fork
        asyncio.create_task(self.notifications.notify_share_forked(
            share=share, forked_by_user_id=identity.user_id,
            new_conversation_id=new_conv.id,
        ))

        return ForkResponse(
            conversation_id=new_conv.id,
            parent_conversation_id=source.id,
            forked_from_share_id=share.share_id,
            fork_message_count=new_message_count,
            title=new_conv.title,
            folder=new_conv.folder,
            created_at=new_conv.created_at,
            user_id=identity.user_id,
        )
```

#### 2.3.1 The message-copy helper

```python
# services/ai-backend/src/agent_runtime/persistence/message_copy.py  (new, ~80 LOC)

async def copy_messages_for_fork(
    *,
    persistence: AsyncPersistencePort,
    encryption: FieldEncryption,
    source_messages: Sequence[MessageRecord],   # already ordered by created_at ASC
    target_conv: ConversationRecord,
    actor_user_id: str,
    clock: Clock,
) -> int:
    """
    Returns the count of inserted rows.

    Invariants:
      • new IDs everywhere (id, parent_message_id rewritten via id_map)
      • run_id, source_message_id, branch_id reset to NULL
      • content_text re-encrypted with a fresh IV via FieldEncryption.encrypt
      • created_at = clock.now() so retention sweeper sees the fork's age,
        not the source's (preserves original via metadata_json.original_created_at)
      • deleted_at IS NOT NULL rows skipped at the read stage (caller's clamp)
    """
    id_map: dict[str, str] = {}
    inserted = 0
    now = clock.now()

    for source in source_messages:
        new_id = new_message_id()
        id_map[source.id] = new_id

        new_parent_id = (
            id_map.get(source.parent_message_id) if source.parent_message_id else None
        )
        # If parent isn't in the copied set (data integrity), null it and warn.
        if source.parent_message_id and new_parent_id is None:
            logger.warning(
                "fork_orphan_parent",
                extra={"src_conv": source.conversation_id, "src_msg": source.id},
            )

        new_record = source.model_copy(update={
            "id": new_id,
            "conversation_id": target_conv.id,
            "org_id": target_conv.org_id,
            "user_id": (
                actor_user_id if source.role == "user" else source.user_id
            ),  # user-typed historical msgs become the forker's; assistant rows keep null
            "run_id": None,
            "source_message_id": None,
            "branch_id": None,
            "parent_message_id": new_parent_id,
            "created_at": now,
            "edited_at": None,
            "metadata_json": {
                **(source.metadata_json or {}),
                "original_created_at": source.created_at.isoformat(),
                "original_message_id": source.id,
                "original_conversation_id": source.conversation_id,
            },
        })

        # Re-encrypt content_text + content_json + attachments_json + quote_json.
        # The codec already knows when to no-op (e.g. NullFieldEncryption).
        new_record = _reencrypt_in_place(new_record, encryption)

        await persistence.insert_message(new_record)
        inserted += 1

    return inserted
```

`_reencrypt_in_place` calls `FieldEncryption.decrypt(value, table, column, org_id)` then `.encrypt(...)` for each PII column. Both `target_conv.org_id` and `source.org_id` are the same in v1 (cross-org forks are out of scope), so the codec's per-org DEK pool is consistent.

The two new modules live under `agent_runtime/persistence/` (codec orchestration) and `runtime_api/services/` (transactional service). Routing is a thin file:

```python
# services/ai-backend/src/runtime_api/http/share_fork_routes.py  (new, ~30 LOC)
# Mounted under the same prefix as PR 6.1's share routes.

@router.post("/v1/agent/shares/{share_token}/fork", response_model=ForkResponse)
async def fork_share(
    share_token: str,
    body: ForkRequest,
    identity: TrustedRequestIdentity = Depends(authenticated_identity),
    service: ConversationForkService = Depends(get_fork_service),
) -> ForkResponse:
    return await service.fork(
        share_token=share_token,
        identity=identity,
        title=body.title,
        folder=body.folder,
    )
```

The facade adds one new proxy line in `share_routes.py` (the file PR 6.1 introduces). Identity headers pass through unchanged.

### 2.4 Audit

One new `action` constant on `WorkerAuditEmitter`:

```python
class _ForkActions:
    CONVERSATION_FORK = "conversation.fork"
```

Metadata (after the chain emitter's standard redactor):

```jsonc
{
  "source_conversation_id": "conv_…src",
  "source_org_id": "org_…",
  "share_id": "share_…",
  "snapshot_at": "2026-05-05T18:01:14.220Z",
  "message_count": 14,
  "target_conversation_id": "conv_…fork",
  "target_user_id": "user_…recipient",
}
```

The chain semantics are identical to PR 6.1's six new actions — append-only, HMAC `prev_hash`, per-org chain ([`migrations/0003_audit_hardening.sql`](../../services/ai-backend/migrations/0003_audit_hardening.sql)). The verifier and SIEM exporter need no code change.

### 2.5 Permissions

Same matrix as PR 6.1's recipient view. The fork endpoint does not require any _additional_ privilege beyond viewing the share — if you can read it, you can fork it. (Forks are own-data; the recipient owns the new conversation; nothing leaks.)

| Caller                                                      | Can fork?                                                                     |
| ----------------------------------------------------------- | ----------------------------------------------------------------------------- |
| Workspace member, share `view_access="workspace"`, same org | ✅                                                                            |
| Listed recipient on a `view_access="specific"` share        | ✅                                                                            |
| Foreign-org caller                                          | 404 (no leak)                                                                 |
| Non-listed recipient on `view_access="specific"`            | 403                                                                           |
| Caller after share revoked / expired                        | 404                                                                           |
| Caller after source conversation soft-deleted               | 404 (the snapshot read returns no source; we refuse to fork an absent source) |

### 2.6 Error semantics

| Condition                                                        | Status | Code                        |
| ---------------------------------------------------------------- | ------ | --------------------------- |
| Share token unknown / revoked / expired                          | 404    | `share_not_found`           |
| Cross-org fork attempt                                           | 404    | `share_not_found` (no leak) |
| Specific-share, caller not a recipient                           | 403    | `share_not_for_recipient`   |
| Source conversation soft-deleted between share creation and fork | 404    | `share_not_found`           |
| Source has more than `RUNTIME_FORK_MAX_MESSAGES` messages        | 422    | `fork_too_large`            |
| `title` longer than 240 chars                                    | 422    | `invalid_title`             |
| `folder` longer than 64 chars                                    | 422    | `invalid_folder`            |
| Persistence failure mid-copy                                     | 500    | rolled back, no partial     |

### 2.7 Frontend contract

Additive:

```ts
// packages/api-types/src/index.ts

export interface ForkRequest {
  title?: string | null;
  folder?: string | null;
}

export interface ForkResponse {
  conversation_id: string;
  parent_conversation_id: string;
  forked_from_share_id: string;
  fork_message_count: number;
  title: string;
  folder: string | null;
  created_at: string;
  user_id: string;
}
```

One new function in [`apps/frontend/src/api/agentApi.ts`](../../apps/frontend/src/api/agentApi.ts):

```ts
forkShare(shareToken: string, request: ForkRequest, identity): Promise<ForkResponse>;
```

The existing `Conversation` type already gains `parent_conversation_id` from PR 1.6; we add `forked_from_share_id` as an optional field (`string | null`).

### 2.8 What the FE actually does — three lines of glue

`features/share/ShareScreen.tsx` (PR 6.1) lands with the "Open in your chat" button disabled and a tooltip. PR 6.2 enables the button and wires:

```ts
// features/share/ShareScreen.tsx — diff against the PR 6.1 stub

const onFork = useCallback(async () => {
  setForking(true);
  try {
    const fork = await forkShare(shareToken, {}, identity);
    toast.success(`Opened with ${fork.fork_message_count} prior turns.`);
    navigate(`/?conversationId=${fork.conversation_id}`);
  } catch (e) {
    toast.error(forkErrorCopy(e));   // 404 / 403 / 422 mapped to copy
    setForking(false);
  }
}, [shareToken, identity, navigate]);

// in JSX
<Button onClick={onFork} loading={forking} disabled={forking}>
  Open in your chat
</Button>
```

That is the entire FE delta. The destination route is the existing `ChatScreen`, which already loads any `conversationId` in the URL (PR 2.2 sidebar wiring). No new screens. No new global state. No new hooks beyond `useNavigate` and the `forkShare` call.

---

## 3 · Architecture

### 3.1 Where this lives in the system

```
                    ┌──────────────────────────────────────────────────────────┐
                    │  apps/frontend                                           │
                    │                                                          │
                    │   features/share/ShareScreen.tsx                         │
                    │     └─ "Open in your chat" → forkShare(token)            │
                    │                       │                                  │
                    │                       └─► navigate("/?conversationId=…") │
                    │                                       │                  │
                    │   ChatScreen.tsx                      ▼                  │
                    │     └─ existing conv load + composer (no change)         │
                    └─────────────────────────────────────┬────────────────────┘
                                                          │
                                                          │  POST /v1/agent/shares/{token}/fork
                                                          ▼
                          ┌───────────────────────────────────────────┐
                          │  backend-facade                           │  thin proxy.
                          │  share_routes.py (one new line)           │  identity headers.
                          └─────────────────────┬─────────────────────┘
                                                │ /internal/v1/agent/shares/{token}/fork
                                                ▼
                          ┌───────────────────────────────────────────┐
                          │  ai-backend  (runtime_api)                │
                          │                                           │
                          │  http/share_fork_routes.py (~30 LOC)      │
                          │   ↳ ConversationForkService (~110 LOC)    │
                          │       ├─ resolve share (PR 6.1 helper)    │
                          │       ├─ recipient gate (PR 6.1 helper)   │
                          │       ├─ ConversationsService.create      │
                          │       ├─ copy_messages_for_fork           │
                          │       └─ WorkerAuditEmitter.emit_         │
                          │             conversation_fork             │
                          │                                           │
                          │  ALL OF THE ABOVE in one DB transaction.  │
                          └────────────┬───────────┬──────────────────┘
                writes new conv +     │           │ reads source snapshot
                copied messages       ▼           ▼
                  ┌──────────────────────┐   ┌──────────────────────┐
                  │ agent_conversations  │   │ agent_messages       │
                  │  + parent_conv_id    │   │ (existing, RLS)      │
                  │  + forked_from_      │   └──────────────────────┘
                  │     share_id         │
                  │ (this PR migration)  │
                  └──────────────────────┘
                  ┌──────────────────────┐   ┌──────────────────────┐
                  │ runtime_audit_log    │   │ conversation_shares  │
                  │  conversation.fork   │   │ (read-only ref to    │
                  │  (existing chain)    │   │  validate the token) │
                  └──────────────────────┘   └──────────────────────┘
```

Same shape as PR 1.6 / 6.1: **one new service file, one new route file, one new migration, one DRY-up of a copy primitive, one FE callsite.** Everything else is reused.

### 3.2 Streaming impact — explicitly **none**

This is the question the user always flags. For fork, the answer is unequivocal:

| Subsystem                                  | Touched?                                                                                                                                                                        |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `runtime_events` schema                    | **No.** No new `event_type`.                                                                                                                                                    |
| `RuntimeEventEnvelope` Pydantic            | **No.**                                                                                                                                                                         |
| SSE handshake (`?after_sequence=N`)        | **No.** Fork is a synchronous HTTP call; no streaming attached.                                                                                                                 |
| Worker `runtime_worker/` job loop          | **No.** Fork doesn't enqueue runs.                                                                                                                                              |
| Agent harness (LangGraph + DeepAgents)     | **No.** First-prompt-after-fork enqueues a run via the existing `RunService.create_run` path; the harness sees a normal new conversation with prior history seeded as messages. |
| Capabilities middleware, MCP loader, tools | **No.** Connector resolution at run-create reuses PR 1.6's chain (request → conversation → workspace_defaults).                                                                 |
| Citations / drafts / approvals / subagents | **No.** Their tables aren't read or written by the fork operation.                                                                                                              |
| Audit chain                                | **Additive.** One new `action`. No chain semantic change.                                                                                                                       |
| Notification dispatcher                    | **Additive.** One new method `notify_share_forked` on the existing Protocol; default impl logs.                                                                                 |

**The fork never produces a streaming event.** It is an HTTP-CRUD operation; the recipient's first prompt is what produces the first SSE stream on the new conversation, exactly as for any new chat.

### 3.3 The single transaction

The most important architectural choice. The fork is a single Postgres transaction whose body is:

```
SAVEPOINT fork_begin

  INSERT INTO agent_conversations (id, org_id, user_id, …, parent_conversation_id, forked_from_share_id, …)
                                                       VALUES (new_conv_id, …)

  -- N message inserts; N is bounded by RUNTIME_FORK_MAX_MESSAGES (default 500)
  INSERT INTO agent_messages (id, conversation_id, …)  VALUES (m1, new_conv_id, …)
  INSERT INTO agent_messages …                          VALUES (m2, new_conv_id, …)
  …

  INSERT INTO runtime_audit_log (id, org_id, action='conversation.fork', metadata_json_redacted, …)

COMMIT
```

The `transaction(org_id=...)` context (`AsyncPersistencePort.transaction`) wraps `SET LOCAL app.current_org_id = '…'` so RLS is satisfied for every statement in the block. This is the same pattern PR 6.1's recipient endpoint uses for the cross-org GUC switch.

If any single statement fails — encryption error, FK violation, audit chain HMAC mismatch — the entire transaction rolls back and the fork is a no-op. AC-9 covers this with a fault-injection test.

The transaction is bounded (≤ 502 statements at the cap). p99 of the full operation in local profiling targets < 250 ms for a 100-message thread; < 800 ms for the 500-message cap. (One round trip per insert — we don't bother with `COPY` because the encryption call is per-row.)

### 3.4 Why we don't copy events / drafts / citations / subagent results

This is a deliberate architectural restraint. Each is a runnable artefact tied to a `run_id`. Carrying them into a fork either:

1. Forces invented `run_id`s (audit-tagged with "synthetic-fork-…"), polluting the run table with rows that have no execution history, or
2. Forces the FE to special-case "is this run synthetic" everywhere it reads runs.

Both options leak v6 implementation detail throughout the runtime. The clean alternative — only copy messages — has one cosmetic cost: citation chips render as muted text in copied turns. We accept that cost in v1, document it in copy ("Source links available in the original chat"), and capture per-source-citation copy as PR 6.2.1 once we have a need for chip continuity.

The user's source-of-truth for "what did Atlas say" is **the message body**, not the activity timeline. Forks preserve that. Activity timelines for prior turns are still navigable via the share view; the recipient can keep the share tab open if they want.

### 3.5 Cross-org / cross-tenancy posture

PR 6.1 enforces the cross-org refusal at the recipient view; PR 6.2 inherits that same gate. There is no path in this PR by which a fork could land in an org other than the share's `org_id`. The `agent_conversations` insert hard-codes `org_id = share.org_id`; the recipient identity is verified to belong to that org through the existing `WorkspaceMembershipResolver` ([`agent_runtime/api/membership.py`](../../services/ai-backend/src/agent_runtime/api/membership.py)) that PR 1.4.1 / 6.1 already use.

If a future PR introduces cross-org sharing, it must explicitly extend the recipient gate, the membership lookup, and this fork operation in a coordinated change. The current schema deliberately does not preempt that work — `parent_conversation_id` is a same-table FK; there is no `parent_org_id` column.

### 3.6 DRY — what we reuse vs. what we add

| Concern                                 | Reuse                                                                                                                                                               | Add                                                                                                                       |
| --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| Identity / RBAC                         | `RuntimeServiceAuthenticator` ([`runtime_api/auth.py:20-105`](../../services/ai-backend/src/runtime_api/auth.py))                                                   | —                                                                                                                         |
| Share token resolution + recipient gate | Helpers from PR 6.1's `share_service.py` (`_resolve_share_or_404`, `_recipient_gate_or_403`)                                                                        | direct call sites; no new gate logic                                                                                      |
| Workspace membership                    | `WorkspaceMembershipResolver` (used identically)                                                                                                                    | —                                                                                                                         |
| Conversation creation                   | `ConversationsService.create_conversation` ([`agent_runtime/api/service.py:214`](../../services/ai-backend/src/agent_runtime/api/service.py:214)) — same path as ⌘N | one extra kwarg `parent_conversation_id`, `forked_from_share_id` (already on the record from PR 1.6's column declaration) |
| Run creation + SSE                      | `RunService.create_run`                                                                                                                                             | —                                                                                                                         |
| Connector defaults                      | PR 1.6 model-resolution chain                                                                                                                                       | —                                                                                                                         |
| Persistence pool / migration runner     | `agent_runtime/persistence/schema/migrate.py`                                                                                                                       | one migration                                                                                                             |
| Encryption                              | `FieldEncryption.encrypt/decrypt` ([`persistence/encryption.py:54-119`](../../services/ai-backend/src/agent_runtime/persistence/encryption.py))                     | one helper `_reencrypt_in_place` (~12 LOC)                                                                                |
| Audit chain                             | `WorkerAuditEmitter`                                                                                                                                                | one `action` constant + one `emit_conversation_fork` helper                                                               |
| Notification port                       | `NotificationDispatcher` (PR 6.1 added `notify_share_created`)                                                                                                      | one new method `notify_share_forked`                                                                                      |
| FE state                                | `useNavigate`, `useApi`                                                                                                                                             | —                                                                                                                         |
| FE primitives                           | `Button`, `Toast`                                                                                                                                                   | —                                                                                                                         |
| Facade proxy                            | `share_routes.py` (PR 6.1)                                                                                                                                          | one extra route registration                                                                                              |

**Net new code** — target:

- 1 SQL migration (~25 lines).
- 1 service file `conversation_fork.py` (~110 LOC).
- 1 helper module `message_copy.py` (~80 LOC, includes encryption pump + ID-rewrite).
- 1 route file `share_fork_routes.py` (~30 LOC).
- 1 facade proxy line.
- 3 tests files (~250 LOC fixtures + table-driven).
- 1 contract addition in `api-types/index.ts` (~15 LOC).
- 1 FE callsite extension (~15 LOC diff in `ShareScreen.tsx`).

Total target: **~350 net LOC** including ~150 LOC of test fixtures.

### 3.7 No third-party middleware needed

| Candidate                                         | Why we skip                                                                                                                                                                     |
| ------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sqlalchemy-utils` UUID / Tree types              | We don't use SQLAlchemy ORM. The fork lineage is a single column self-FK with `ON DELETE SET NULL`; PostgreSQL handles it natively.                                             |
| `python-state-machines`                           | Fork has no states — it's a one-shot CRUD. The conversation lifecycle states (`active/archived/deleted`) live on the conversation row and are managed by PR 1.6.                |
| `python-fastcopy` / `dictdiffer` for message copy | The copy is a 4-line `model_copy(update={…})` per Pydantic shape. A library would obscure the per-field reset semantics.                                                        |
| Background-job library (`Celery` / `arq`)         | Fork is bounded (≤500 messages, ≤800 ms p99) and synchronous. A background job would force a "fork pending" state in the FE we don't otherwise need.                            |
| `python-jsonpatch` for `metadata_json` merge      | One `{**existing, ...new_keys}` spread does the job.                                                                                                                            |
| `bulk_insert_mappings` / `psycopg.copy`           | The encryption codec call is per-row; bulk insert would force a two-pass encrypt-then-copy that loses transaction simplicity. The 500-row cap keeps per-insert cost negligible. |
| FE: any "duplicate this thing" widget library     | The button is a `Button` with a click handler. A library would be net-negative.                                                                                                 |

### 3.8 Sequence — Marcus forks Sarah's share

```
Marcus              FE                         facade               ai-backend                                  Postgres
  │                 │                            │                     │                                          │
  │  click          │                            │                     │                                          │
  │ "Open in       │                            │                     │                                          │
  │  your chat"     │                            │                     │                                          │
  │ ──────────────►│                            │                     │                                          │
  │                 │ POST /v1/agent/shares/    │                     │                                          │
  │                 │   {token}/fork  {}         │                     │                                          │
  │                 │ ──────────────────────────►│ /internal/v1/agent/shares/{token}/fork                       │
  │                 │                            │ ──────────────────►│  ConversationForkService.fork()         │
  │                 │                            │                     │                                          │
  │                 │                            │                     │  resolve share (token hash lookup,       │
  │                 │                            │                     │   org-agnostic)                          │
  │                 │                            │                     │  recipient gate (workspace OR specific) │
  │                 │                            │                     │                                          │
  │                 │                            │                     │  BEGIN TRANSACTION                       │
  │                 │                            │                     │  SET LOCAL app.current_org_id = share.org│
  │                 │                            │                     │                                          │
  │                 │                            │                     │  SELECT * FROM agent_conversations      │
  │                 │                            │                     │   WHERE id = share.conversation_id       │
  │                 │                            │                     │ ────────────────────────────────────────►│
  │                 │                            │                     │  ◄────── source row                      │
  │                 │                            │                     │                                          │
  │                 │                            │                     │  SELECT * FROM agent_messages            │
  │                 │                            │                     │   WHERE conversation_id = …               │
  │                 │                            │                     │     AND created_at <= share.snapshot_at  │
  │                 │                            │                     │     AND deleted_at IS NULL               │
  │                 │                            │                     │   ORDER BY created_at ASC                │
  │                 │                            │                     │   LIMIT 501                              │
  │                 │                            │                     │ ────────────────────────────────────────►│
  │                 │                            │                     │  ◄────── 14 rows                         │
  │                 │                            │                     │                                          │
  │                 │                            │                     │  INSERT INTO agent_conversations         │
  │                 │                            │                     │   (id, org_id, user_id=Marcus,           │
  │                 │                            │                     │    parent_conversation_id=src.id,        │
  │                 │                            │                     │    forked_from_share_id=share.share_id, │
  │                 │                            │                     │    enabled_connectors='{}'::jsonb)       │
  │                 │                            │                     │ ────────────────────────────────────────►│
  │                 │                            │                     │                                          │
  │                 │                            │                     │  for each src_msg in 14:                 │
  │                 │                            │                     │    decrypt content_text  (FieldEnc)      │
  │                 │                            │                     │    rewrite parent_message_id (id_map)    │
  │                 │                            │                     │    encrypt content_text  (fresh IV)      │
  │                 │                            │                     │    INSERT INTO agent_messages            │
  │                 │                            │                     │ ────────────────────────────────────────►│
  │                 │                            │                     │  (14 inserts; ≤ ~600 ms total at the cap)│
  │                 │                            │                     │                                          │
  │                 │                            │                     │  WorkerAuditEmitter.emit_conversation_   │
  │                 │                            │                     │    fork(...)  →  INSERT runtime_audit_log│
  │                 │                            │                     │ ────────────────────────────────────────►│
  │                 │                            │                     │                                          │
  │                 │                            │                     │  COMMIT                                  │
  │                 │                            │                     │                                          │
  │                 │                            │                     │  asyncio.create_task(notify_share_       │
  │                 │                            │                     │    forked)  ── fire-and-forget            │
  │                 │                            │                     │                                          │
  │                 │ ◄──────────────────────── │ ◄────── ForkResponse {conv_id, fork_message_count: 14, …}      │
  │                 │                            │                     │                                          │
  │                 │  navigate("/?conversationId=conv_…")              │                                          │
  │                 │  toast.success("Opened with 14 prior turns.")     │                                          │
  │                 │                                                                                              │
  │                 │  ChatScreen mounts → loadConversationById → existing flow                                     │
  │                 │   (sidebar shows new chat, composer ready, no special "fork" mode)                           │
  │                 │                                                                                              │
  │  type a prompt │                                                                                              │
  │ ──────────────►│ POST /v1/agent/runs (existing, unchanged)                                                    │
  │                 │  → SSE stream, harness builds context from copied messages + new prompt                     │
```

The only ai-backend code touched on the prompt-after-fork side is the existing `RunService.create_run` plus its connector-resolution chain (PR 1.6). The fork is invisible to the harness — it's a new conversation with seeded history, semantically indistinguishable from "I pasted these turns into a new chat."

### 3.9 Edge cases

| Case                                                                                | Behaviour                                                                                                                                                                                                                                                                          |
| ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Fork before the recipient has viewed the share                                      | Allowed. The recipient gate is the same; there's no "must have viewed first" requirement. Audit chain shows fork before any `share.viewed`.                                                                                                                                        |
| Recipient forks twice                                                               | Two independent conversations land in their sidebar. Each has its own `parent_conversation_id` pointing to the same source. Acceptable; the user can delete one.                                                                                                                   |
| Source thread had pending approvals at `snapshot_at`                                | Approval rows are not copied. The fork starts with a clean approval slate; if the recipient prompts an action that triggers approval, a new approval row is created in the fork's conversation as normal.                                                                          |
| Source thread had a `runtime_drafts` row at `snapshot_at`                           | Not copied. The recipient's first prompt can ask Atlas to "rewrite the announcement" and a new draft is created in the fork.                                                                                                                                                       |
| `forked_from_share_id` references a share that was later revoked                    | The column survives. `parent_conversation_id` survives unless the source conversation itself is deleted (then `ON DELETE SET NULL`). Audit row remains immutable.                                                                                                                  |
| Two forks at the same `snapshot_at` from two different recipients                   | Two independent rows. No contention.                                                                                                                                                                                                                                               |
| Source conversation has a `parent_conversation_id` (i.e. it's itself a fork)        | Fork allowed; the new fork's `parent_conversation_id` points to _that_ conversation, not the original. Multi-hop chains form naturally; no special handling.                                                                                                                       |
| Caller's session has an `org_id` they recently joined (membership change in flight) | Membership resolver caches up to its TTL; in worst case a 403 surfaces and the caller retries. Documented; not a blocker.                                                                                                                                                          |
| Encryption codec rotates keys between source and fork                               | `FieldEncryption` carries `key_version` per envelope. Decrypting the source uses the source's key version; encrypting the fork uses the _current_ key version. Consistent with how every other writer works.                                                                       |
| Source conversation deletion races with the in-flight fork                          | The TX `SELECT` returns the source row only if visible. If a `DELETE` (PR 1.6 soft-delete sets `deleted_at`) runs first, the fork errors with `share_not_found`. If `DELETE` runs after the TX commits, fork is fine and the FK becomes NULL when the source is later hard-reaped. |

### 3.10 Test plan (lives in this PR)

**ai-backend (`services/ai-backend/tests/`)**

- `unit/agent_runtime/persistence/test_message_copy.py`
  - 0/1/many message copy
  - parent rewrite (linear chain, branching, orphan parent → null + warn)
  - role-based `user_id` assignment (recipient on `user` rows, null on `assistant`)
  - `run_id`/`source_message_id`/`branch_id` reset
  - encryption round-trip with a non-null `FieldEncryption`
  - cap exceeded (501 source messages → raises `ForkError.FORK_TOO_LARGE`)
- `unit/runtime_api/services/test_conversation_fork_service.py`
  - happy path workspace; happy path specific
  - share not found / revoked / expired → 404
  - cross-org caller → 404
  - non-listed recipient on specific → 403
  - source soft-deleted → 404
  - `RUNTIME_FORK_MAX_MESSAGES` boundary (500 OK, 501 → 422)
  - audit row content + chain seq advancement
  - rollback on injected encryption-codec failure
- `unit/runtime_api/http/test_fork_route.py`
  - identity propagation
  - request validation (title length, folder length, body absence ⇒ defaults)
- `integration/test_fork_then_run.py`
  - full flow: PR 6.1 create share → PR 6.2 fork → POST /runs → SSE completes
  - assert seeded messages appear in the model's prior context but not in `agent_runs` association
- `integration/test_fork_audit_chain.py` — chain verifier passes after `share.created → share.viewed → conversation.fork → run.completed`

**Frontend (`apps/frontend/src/`)**

- `features/share/ShareScreen.test.tsx` (extended)
  - button enabled iff `share.view_access` matches recipient (always, in v1)
  - click → `forkShare` called with the right `(token, {})`
  - on success → router push to `/?conversationId=…`, toast surfaces `fork_message_count`
  - on 422 `fork_too_large` → friendly copy
  - on 404 → "share has been revoked" copy
  - on 403 → "this share isn't for you" copy
- `api/agentApi.test.ts` (extended) — `forkShare` shape

**Cross-service smoke (`make test`)** — one happy path: create share → fork → send a prompt → SSE event sequence terminates cleanly.

### 3.11 Rollout

- **Flag-free.** The new column starts NULL on existing rows; the new index is sparse; the new constraint adds no row-level work for old rows.
- **Zero-downtime migration.** `ALTER TABLE … ADD COLUMN IF NOT EXISTS … TEXT` (no default → no rewrite). `ADD CONSTRAINT … FOREIGN KEY … NOT VALID` is _not_ used here — the column is empty so `ADD CONSTRAINT` is fast; for a large table the runbook addendum is to use `NOT VALID` + later `VALIDATE CONSTRAINT` if profiling shows lock pressure. New index is `CREATE INDEX IF NOT EXISTS` (CONCURRENTLY in production runbook).
- **Backout.** Drop the constraint, drop the index, drop the column; the fork endpoint returns 503 (the service requires the column at write time). FE gracefully shows a degraded state via the existing API-error toast.
- **Forward compatibility.** Multi-hop fork chains are supported by the schema today; any future PR that wants to query "all descendants of conversation X" can add a recursive CTE without a migration.
- **Optional escape hatch.** `RUNTIME_FORK_ENABLED=true|false` env on ai-backend can short-circuit the route with 503 for tenants that haven't approved sharing yet (pairs with PR 6.1's `RUNTIME_SHARING_ENABLED` flag).

### 3.12 Open questions

1. **Bulk fork.** Some workflows might want "fork these 5 conversations into a folder". Out of scope; the per-conversation endpoint composes if a future UI needs it.
2. **Citation chip continuity.** Tracked as PR 6.2.1 (copy `runtime_citations` rows scoped to the fork). v1 ships with muted-text fallback.
3. **Fork visibility to source creator.** PR 4.1's notification matrix decides whether the creator sees a "Marcus forked your chat" toast/email/Slack. v1 default: off; opt-in.
4. **Fork-and-edit-on-paste.** Some users may want to fork _and_ immediately edit one of the historical user messages. v1: fork as-is; user manually edits afterwards (the existing edit-with-re-run flow on user messages is a P1 future feature, not a fork dependency).

---

## 4 · Acceptance checklist

- [ ] Migration `0023_conversation_fork_lineage.sql` applies cleanly forward (FK + index + new column) and rolls back.
- [ ] `copy_messages_for_fork` handles 0/1/many message graphs; parent rewrite is correct; encryption uses fresh IVs (no envelope memcpy).
- [ ] `ConversationForkService.fork` is one TX; rollback on injected codec failure leaves zero rows.
- [ ] `RUNTIME_FORK_MAX_MESSAGES` is enforced; default 500.
- [ ] Recipient gate identical to PR 6.1 — workspace, specific, cross-org, revoked, expired all map to the same status codes.
- [ ] Source soft-delete after fork severs `parent_conversation_id` to NULL; `forked_from_share_id` survives.
- [ ] Audit row `conversation.fork` carries `{source_conversation_id, source_org_id, share_id, snapshot_at, message_count, target_conversation_id, target_user_id}`. Chain verifier passes.
- [ ] No new event type. `RuntimeEventEnvelope` Pydantic schema byte-identical pre/post merge.
- [ ] `backend-facade/share_routes.py` registers `POST /v1/agent/shares/{share_token}/fork`; identity headers preserved; never reaches `/internal/v1/*`.
- [ ] `@0x-copilot/api-types` exports `ForkRequest` and `ForkResponse`. `Conversation` gains `forked_from_share_id?: string | null`.
- [ ] `apps/frontend/src/features/share/ShareScreen.tsx` enables "Open in your chat" button; click calls `forkShare`, navigates to `/?conversationId=…`, surfaces toast.
- [ ] `apps/frontend/src/api/agentApi.ts` exports `forkShare`.
- [ ] First prompt in a forked conversation runs through the existing SSE pipeline with no special branch.
- [ ] `make test` green; ai-backend full suite green; frontend typecheck + build green.

---

## 5 · References

- Atlas Design Doc handoff bundle, §"Flow — Share" (step 3) — "fork creates a new chat that starts where the share left off but uses the recipient's connector set going forward."
- [`docs/architecture/runtime-stream-handshake.md`](../architecture/runtime-stream-handshake.md) — unchanged by this PR.
- [`docs/architecture/service-boundaries.md`](../architecture/service-boundaries.md).
- [`services/ai-backend/migrations/0008_rls_tenant_isolation.sql`](../../services/ai-backend/migrations/0008_rls_tenant_isolation.sql) — RLS pattern reused.
- [`services/ai-backend/migrations/0011_field_encryption.sql`](../../services/ai-backend/migrations/0011_field_encryption.sql) — encryption envelope reused.
- [`services/ai-backend/src/agent_runtime/persistence/encryption.py`](../../services/ai-backend/src/agent_runtime/persistence/encryption.py) `FieldEncryption` — codec reused for re-encrypt-on-copy.
- [`services/ai-backend/src/agent_runtime/api/membership.py`](../../services/ai-backend/src/agent_runtime/api/membership.py) `WorkspaceMembershipResolver` — recipient validation reused.
- [`services/ai-backend/src/runtime_worker/audit.py`](../../services/ai-backend/src/runtime_worker/audit.py) `WorkerAuditEmitter` — extended with one constant.
- [`services/ai-backend/src/agent_runtime/api/notifications.py`](../../services/ai-backend/src/agent_runtime/api/notifications.py) `NotificationDispatcher` — extended with one method.
- [`services/ai-backend/src/runtime_api/auth.py`](../../services/ai-backend/src/runtime_api/auth.py) `RuntimeServiceAuthenticator` — identity parsing reused.
- [`services/backend-facade/src/backend_facade/workspace_routes.py`](../../services/backend-facade/src/backend_facade/workspace_routes.py) `_forward` — proxy pattern reused (the route is added to `share_routes.py` from PR 6.1).
- [`packages/service-contracts/src/copilot_service_contracts/headers.py`](../../packages/service-contracts/src/copilot_service_contracts/headers.py) — header constants reused.
- [`apps/frontend/src/api/agentApi.ts`](../../apps/frontend/src/api/agentApi.ts) — one new client function.
- [`docs/new-design/pr-1.6-workspace-defaults-conversation-lifecycle.md`](pr-1.6-workspace-defaults-conversation-lifecycle.md) — sibling PR; provides forward-declared `parent_conversation_id` column and the model-resolution chain the fork's first run depends on.
- [`docs/new-design/pr-6.1-conversation-sharing.md`](pr-6.1-conversation-sharing.md) — sibling PR; provides the share resolution + recipient-gate helpers this PR composes.
- [`docs/new-design/pr-1.2-per-chat-connector-scope.md`](pr-1-2-per-chat-connector-scope.md) — sibling PR; provides the `enabled_connectors` shape that the fork starts with empty (so workspace_defaults cleanly applies).
- [PostgreSQL — ON DELETE clause](https://www.postgresql.org/docs/current/ddl-constraints.html#DDL-CONSTRAINTS-FK) — `ON DELETE SET NULL` semantics.

"""Account-merge re-key for the in-memory runtime store (PRD §6.3/§6.4).

Walks every tenant-scoped structure on :class:`InMemoryRuntimeApiStore` (and
the in-memory satellite stores when supplied) and rewrites the absorbed
account's tenancy fields to the survivor's. Rules mirror the Postgres
re-keyer:

- primary keys / run ids / conversation ids are never rewritten — only
  ``org_id`` and user-owner columns that equal the absorbed user;
- the hash-chained audit log is never rewritten (the caller appends a merge
  marker to the survivor chain instead);
- run event envelopes carry no tenancy fields, so per-run ``sequence_no``
  ordering is untouched by construction;
- unique-key collisions resolve conservatively: survivor row wins, daily
  usage rollups SUM-merge, colliding idempotency keys are dropped — each
  resolution is recorded as a warning.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:  # pragma: no cover - typing only
    from runtime_adapters.in_memory.citation_store import InMemoryCitationStore
    from runtime_adapters.in_memory.conversation_tool_ordinal_store import (
        InMemoryConversationToolOrdinalStore,
    )
    from runtime_adapters.in_memory.draft_store import InMemoryDraftStore
    from runtime_adapters.in_memory.runtime_api_store import InMemoryRuntimeApiStore
    from runtime_adapters.in_memory.share_store import InMemoryShareStore
    from runtime_adapters.in_memory.todo_extraction_store import (
        InMemoryTodoExtractionStore,
    )


class InMemoryAccountMergeRekeyer:
    """Rewrite one absorbed account's records to the survivor account.

    Instantiate per merge; :meth:`rekey_store` and the satellite helpers
    accumulate per-structure moved-row counts and collision warnings on the
    instance. Counts use the Postgres table names where a table exists so
    both backends produce a comparable ``tables`` response.
    """

    #: Owner-column catalog. A field is rewritten only when it equals the
    #: absorbed user id AND the record belongs to the absorbed org — the
    #: same guard the SQL re-keyer applies per table.
    _USER_ID_FIELDS = frozenset(
        {
            "user_id",
            "owner_user_id",
            "requested_by_user_id",
            "decided_by_user_id",
            "created_by_user_id",
            "updated_by_user_id",
            "released_by_user_id",
            "forwarded_to_user_id",
        }
    )

    #: Counter fields SUM-merged when two daily-rollup rows collide.
    _ROLLUP_SUM_FIELDS = frozenset({"runs_count", "call_count"})

    def __init__(
        self,
        *,
        absorbed_org_id: str,
        absorbed_user_id: str,
        survivor_org_id: str,
        survivor_user_id: str,
    ) -> None:
        self._absorbed_org = absorbed_org_id
        self._absorbed_user = absorbed_user_id
        self._survivor_org = survivor_org_id
        self._survivor_user = survivor_user_id
        self.tables: dict[str, int] = {}
        self.warnings: list[str] = []

    # ----- record-level rewrite ------------------------------------------

    def _rekey_record(self, record: BaseModel) -> BaseModel | None:
        """Return a re-keyed copy of *record*, or ``None`` when out of scope.

        Only records whose ``org_id`` equals the absorbed org are touched;
        user-owner fields are rewritten only where they equal the absorbed
        user. Nested ``runtime_context`` identity (RunRecord / queue
        commands) is rewritten alongside so scoped re-reads keep working.
        """

        fields = type(record).model_fields
        if "org_id" not in fields or getattr(record, "org_id") != self._absorbed_org:
            return None
        updates: dict[str, object] = {"org_id": self._survivor_org}
        for name in self._USER_ID_FIELDS:
            if name in fields and getattr(record, name) == self._absorbed_user:
                updates[name] = self._survivor_user
        if "runtime_context" in fields:
            context = getattr(record, "runtime_context")
            if isinstance(context, BaseModel):
                context_updates: dict[str, object] = {}
                if getattr(context, "org_id", None) == self._absorbed_org:
                    context_updates["org_id"] = self._survivor_org
                if getattr(context, "user_id", None) == self._absorbed_user:
                    context_updates["user_id"] = self._survivor_user
                if context_updates:
                    updates["runtime_context"] = context.model_copy(
                        update=context_updates
                    )
        return record.model_copy(update=updates)

    def _count(self, table: str, moved: int) -> None:
        """Accumulate a moved-row count, omitting zero-count structures."""

        if moved:
            self.tables[table] = self.tables.get(table, 0) + moved

    def _warn(self, message: str) -> None:
        self.warnings.append(message)

    def _rekey_mapping(self, table: str, mapping: dict) -> None:
        """Rewrite records stored as ``{key: record}`` in place."""

        moved = 0
        for key, record in list(mapping.items()):
            rekeyed = self._rekey_record(record)
            if rekeyed is not None:
                mapping[key] = rekeyed
                moved += 1
        self._count(table, moved)

    def _rekey_list(self, table: str, items: list) -> None:
        """Rewrite records stored as a list in place."""

        moved = 0
        for index, record in enumerate(items):
            rekeyed = self._rekey_record(record)
            if rekeyed is not None:
                items[index] = rekeyed
                moved += 1
        self._count(table, moved)

    # ----- main store -----------------------------------------------------

    def rekey_store(self, store: InMemoryRuntimeApiStore) -> None:
        """Re-key every tenant-scoped structure on the main in-memory store.

        ``store.audit_log`` and the per-org chain heads are deliberately
        untouched — the audit chain is append-only across both backends.
        ``store.events_by_run`` carries no tenancy fields; events follow
        their run implicitly and keep their ``sequence_no``.
        """

        self._rekey_mapping("agent_conversations", store.conversations)
        self._rekey_mapping("agent_messages", store.messages)
        self._rekey_mapping("agent_runs", store.runs)
        self._rekey_mapping("runtime_approval_requests", store.approval_requests)
        self._rekey_mapping("approval_decisions", store.approval_decisions)
        self._rekey_mapping("runtime_approval_batches", store.approval_batches)
        self._rekey_mapping("runtime_run_usage", store.run_usage)
        self._rekey_list("runtime_model_call_usage", store.model_call_usage)
        self._rekey_list("runtime_compression_events", store.compression_events)
        self._rekey_list("runtime_deletion_evidence", store.deletion_evidence)
        self._rekey_queue(store)
        self._rekey_idempotency(
            "agent_conversations_idempotency", store._conversation_idempotency
        )
        self._rekey_idempotency("agent_runs_idempotency", store._run_idempotency)
        self._rekey_idempotency(
            "agent_runs_idempotency_fingerprint", store._run_idempotency_fingerprint
        )
        self._rekey_user_rollup(store)
        self._rekey_org_rollup("runtime_usage_daily_org", store.org_daily_usage)
        self._rekey_org_rollup(
            "runtime_usage_daily_connector", store.connector_daily_usage
        )
        self._rekey_org_rollup(
            "runtime_usage_daily_subagent", store.subagent_daily_usage
        )
        self._rekey_org_rollup("runtime_usage_daily_purpose", store.purpose_daily_usage)
        self._rekey_tool_completions(store)
        self._rekey_budgets(store)
        self._rekey_tool_budgets(store)
        self._rekey_retention_policies(store)
        self._rekey_workspace_defaults(store)

    def _rekey_queue(self, store: InMemoryRuntimeApiStore) -> None:
        """Rewrite tenancy on queued commands and their outbox payloads.

        FR-M6 quiesce is NOT implemented yet (deferred), so pending
        entries here mean the drain was incomplete — re-key them anyway
        (conservative: never leave absorbed-keyed work) and warn.
        """

        moved = 0
        for commands in (
            store.run_commands,
            store.cancel_commands,
            store.approval_commands,
        ):
            for index, command in enumerate(commands):
                rekeyed = self._rekey_record(command)
                if rekeyed is not None:
                    commands[index] = rekeyed
                    moved += 1
        for payload in store._queue_payloads.values():
            if payload.get("org_id") != self._absorbed_org:
                continue
            payload["org_id"] = self._survivor_org
            if payload.get("user_id") == self._absorbed_user:
                payload["user_id"] = self._survivor_user
            context = payload.get("runtime_context")
            if isinstance(context, dict):
                if context.get("org_id") == self._absorbed_org:
                    context["org_id"] = self._survivor_org
                if context.get("user_id") == self._absorbed_user:
                    context["user_id"] = self._survivor_user
            moved += 1
        if moved:
            self._warn(
                "queue_not_drained: re-keyed pending queue entries for the "
                "absorbed account re-keyed WITHOUT a drain (FR-M6 deferred)"
            )
        self._count("queue_commands", moved)

    def _rekey_idempotency(self, table: str, mapping: dict) -> None:
        """Move ``(org_id, user_id, key)``-keyed idempotency entries.

        A survivor-side entry with the same idempotency key wins — the
        absorbed entry is dropped with a warning. Idempotency keys are
        per-account request dedup, so a cross-account collision only means
        a stale retry could create a fresh row instead of deduping.
        """

        moved = 0
        for key in [k for k in mapping if k[0] == self._absorbed_org]:
            org_id, user_id, idempotency_key = key
            new_key = (
                self._survivor_org,
                self._survivor_user if user_id == self._absorbed_user else user_id,
                idempotency_key,
            )
            value = mapping.pop(key)
            if new_key in mapping:
                self._warn(
                    f"{table}: dropped colliding idempotency key "
                    f"{idempotency_key!r} (survivor entry wins)"
                )
                continue
            mapping[new_key] = value
            moved += 1
        self._count(table, moved)

    def _merge_rollup_rows(self, existing: BaseModel, incoming: BaseModel) -> BaseModel:
        """SUM-merge two colliding daily-rollup rows (trivially correct).

        Token counters and run/call counts add; ``distinct_users`` takes the
        max (both accounts are the same human post-merge); ``cost_micro_usd``
        adds None-aware; ``refreshed_at`` keeps the later stamp.
        """

        updates: dict[str, object] = {}
        for name in type(existing).model_fields:
            current = getattr(existing, name)
            other = getattr(incoming, name)
            if name in self._ROLLUP_SUM_FIELDS or name.endswith("_tokens"):
                updates[name] = int(current) + int(other)
            elif name == "distinct_users":
                updates[name] = max(int(current), int(other))
            elif name == "cost_micro_usd":
                if current is None and other is None:
                    continue
                updates[name] = int(current or 0) + int(other or 0)
            elif name == "refreshed_at":
                updates[name] = max(current, other)
        return existing.model_copy(update=updates)

    def _rekey_user_rollup(self, store: InMemoryRuntimeApiStore) -> None:
        """Move ``(org, user, day, provider, model)``-keyed user rollups."""

        mapping = store.user_daily_usage
        moved = 0
        for key in [
            k
            for k in mapping
            if k[0] == self._absorbed_org and k[1] == self._absorbed_user
        ]:
            row = mapping.pop(key)
            new_key = (self._survivor_org, self._survivor_user, *key[2:])
            rekeyed = row.model_copy(
                update={"org_id": self._survivor_org, "user_id": self._survivor_user}
            )
            if new_key in mapping:
                mapping[new_key] = self._merge_rollup_rows(mapping[new_key], rekeyed)
                self._warn(
                    "runtime_usage_daily_user: SUM-merged colliding rollup "
                    f"row for day {key[2]!r}"
                )
            else:
                mapping[new_key] = rekeyed
            moved += 1
        self._count("runtime_usage_daily_user", moved)

    def _rekey_org_rollup(self, table: str, mapping: dict) -> None:
        """Move org-keyed rollup rows (key shape ``(org, *rest)``)."""

        moved = 0
        for key in [k for k in mapping if k[0] == self._absorbed_org]:
            row = mapping.pop(key)
            new_key = (self._survivor_org, *key[1:])
            rekeyed = row.model_copy(update={"org_id": self._survivor_org})
            if new_key in mapping:
                mapping[new_key] = self._merge_rollup_rows(mapping[new_key], rekeyed)
                self._warn(f"{table}: SUM-merged colliding rollup row")
            else:
                mapping[new_key] = rekeyed
            moved += 1
        self._count(table, moved)

    def _rekey_tool_completions(self, store: InMemoryRuntimeApiStore) -> None:
        """Rewrite the ``(org_id, run_id, connector_slug, completed_at)`` seed tuples."""

        moved = 0
        completions = store.tool_invocation_completions
        for index, entry in enumerate(completions):
            if entry[0] == self._absorbed_org:
                completions[index] = (self._survivor_org, *entry[1:])
                moved += 1
        self._count("tool_invocation_completions", moved)

    def _rekey_budgets(self, store: InMemoryRuntimeApiStore) -> None:
        """Move budgets; the survivor's ``(user-slot, scope, period)`` row wins.

        A dropped budget takes its state and reservation rows with it —
        the same effect as the Postgres ``ON DELETE CASCADE``.
        """

        def _slot(record: BaseModel) -> tuple:
            return (
                getattr(record, "user_id", None) or "<org>",
                getattr(record, "scope"),
                getattr(record, "period"),
            )

        survivor_slots = {
            _slot(b) for b in store.budgets.values() if b.org_id == self._survivor_org
        }
        moved = 0
        for budget_id in [
            bid for bid, b in store.budgets.items() if b.org_id == self._absorbed_org
        ]:
            rekeyed = self._rekey_record(store.budgets[budget_id])
            assert rekeyed is not None
            if _slot(rekeyed) in survivor_slots:
                del store.budgets[budget_id]
                for state_key in [k for k in store.budget_states if k[0] == budget_id]:
                    del store.budget_states[state_key]
                for reservation_id in [
                    rid
                    for rid, r in store.budget_reservations.items()
                    if r.budget_id == budget_id
                ]:
                    del store.budget_reservations[reservation_id]
                self._warn(
                    f"usage_budgets: dropped absorbed budget {budget_id!r} "
                    "(survivor already has a budget in the same slot)"
                )
                continue
            store.budgets[budget_id] = rekeyed
            survivor_slots.add(_slot(rekeyed))
            moved += 1
        self._count("usage_budgets", moved)

    def _rekey_tool_budgets(self, store: InMemoryRuntimeApiStore) -> None:
        """Move per-tool budgets; the survivor's ``tool_name`` slot wins."""

        survivor_tools = {
            b.tool_name
            for b in store.tool_budgets.values()
            if b.org_id == self._survivor_org
        }
        moved = 0
        for budget_id in [
            bid
            for bid, b in store.tool_budgets.items()
            if b.org_id == self._absorbed_org
        ]:
            record = store.tool_budgets[budget_id]
            if record.tool_name in survivor_tools:
                del store.tool_budgets[budget_id]
                self._warn(
                    f"runtime_tool_budgets: dropped absorbed budget for tool "
                    f"{record.tool_name!r} (survivor slot wins)"
                )
                continue
            store.tool_budgets[budget_id] = record.model_copy(
                update={"org_id": self._survivor_org}
            )
            survivor_tools.add(record.tool_name)
            moved += 1
        self._count("runtime_tool_budgets", moved)

    def _rekey_retention_policies(self, store: InMemoryRuntimeApiStore) -> None:
        """Merge the absorbed org's policy bucket; survivor triples win."""

        absorbed_bucket = store.retention_policies.pop(self._absorbed_org, ())
        if not absorbed_bucket:
            return
        survivor_bucket = list(store.retention_policies.get(self._survivor_org, ()))
        survivor_triples = {
            (row.scope, row.resource_id, row.kind) for row in survivor_bucket
        }
        moved = 0
        for row in absorbed_bucket:
            triple = (row.scope, row.resource_id, row.kind)
            if triple in survivor_triples:
                self._warn(
                    "retention_policies: dropped absorbed policy "
                    f"{row.id!r} (survivor policy wins for the same triple)"
                )
                continue
            rekeyed = self._rekey_record(row)
            survivor_bucket.append(rekeyed if rekeyed is not None else row)
            survivor_triples.add(triple)
            moved += 1
        store.retention_policies[self._survivor_org] = tuple(survivor_bucket)
        self._count("retention_policies", moved)

    def _rekey_workspace_defaults(self, store: InMemoryRuntimeApiStore) -> None:
        """Move the single per-org defaults row; the survivor's row wins."""

        record = store.workspace_defaults.pop(self._absorbed_org, None)
        if record is None:
            return
        if self._survivor_org in store.workspace_defaults:
            self._warn(
                "workspace_defaults: dropped absorbed row "
                "(survivor workspace defaults win)"
            )
            return
        rekeyed = self._rekey_record(record)
        store.workspace_defaults[self._survivor_org] = (
            rekeyed if rekeyed is not None else record
        )
        self._count("workspace_defaults", 1)

    # ----- satellite stores ----------------------------------------------

    def rekey_draft_store(self, store: InMemoryDraftStore) -> None:
        """Move ``(org_id, draft_id)``-keyed draft histories to the survivor."""

        moved = 0
        with store._lock:
            for key in [k for k in store.versions if k[0] == self._absorbed_org]:
                history = store.versions.pop(key)
                new_key = (self._survivor_org, key[1])
                if new_key in store.versions:
                    self._warn(
                        f"runtime_drafts: dropped absorbed draft {key[1]!r} "
                        "(survivor draft id collision)"
                    )
                    continue
                rekeyed_history = []
                for record in history:
                    rekeyed = self._rekey_record(record)
                    rekeyed_history.append(rekeyed if rekeyed is not None else record)
                    moved += 1
                store.versions[new_key] = rekeyed_history
        self._count("runtime_drafts", moved)

    def rekey_share_store(self, store: InMemoryShareStore) -> None:
        """Re-key shares and their recipient rows.

        Recipient user ids are rewritten only on shares that belong to the
        merged account; a survivor recipient entry on the same share wins.
        """

        moved = 0
        recipients_moved = 0
        with store._lock:
            for share_id, share in list(store.shares.items()):
                rekeyed = self._rekey_record(share)
                if rekeyed is None:
                    continue
                store.shares[share_id] = rekeyed
                moved += 1
                recipients = store.recipients.get(share_id, {})
                if self._absorbed_user in recipients:
                    record = recipients.pop(self._absorbed_user)
                    if self._survivor_user in recipients:
                        self._warn(
                            "conversation_share_recipients: dropped absorbed "
                            f"recipient on share {share_id!r} (survivor wins)"
                        )
                    else:
                        recipients[self._survivor_user] = record.model_copy(
                            update={"user_id": self._survivor_user}
                        )
                        recipients_moved += 1
        self._count("conversation_shares", moved)
        self._count("conversation_share_recipients", recipients_moved)

    def rekey_tool_ordinal_store(
        self, store: InMemoryConversationToolOrdinalStore
    ) -> None:
        """Rewrite ``org_id`` on ordinal bindings (PK is conversation-scoped)."""

        moved = 0
        for key, record in list(store._by_pk.items()):
            rekeyed = self._rekey_record(record)
            if rekeyed is not None:
                store._by_pk[key] = rekeyed
                moved += 1
        self._count("agent_conversation_tool_ordinals", moved)

    def rekey_citation_store(self, store: InMemoryCitationStore) -> None:
        """Rewrite ``org_id`` on citation rows (PK is ``(run_id, citation_id)``)."""

        moved = 0
        with store._lock:
            for index, record in enumerate(store._rows):
                rekeyed = self._rekey_record(record)
                if rekeyed is not None:
                    store._rows[index] = rekeyed
                    moved += 1
            for key, record in list(store._index.items()):
                rekeyed = self._rekey_record(record)
                if rekeyed is not None:
                    store._index[key] = rekeyed
        self._count("runtime_citations", moved)

    def rekey_todo_extraction_store(self, store: InMemoryTodoExtractionStore) -> None:
        """Rewrite ``org_id`` / ``owner_user_id`` on extraction proposals."""

        moved = 0
        with store._lock:
            for extraction_id, record in list(store.rows.items()):
                rekeyed = self._rekey_record(record)
                if rekeyed is not None:
                    store.rows[extraction_id] = rekeyed
                    moved += 1
        self._count("todo_extractions", moved)


__all__ = ("InMemoryAccountMergeRekeyer",)

"""Most-specific retention policy resolution.

Specificity order: ``conversation > assistant > user > org > default``. The
resolver folds a flat list of ``RetentionPolicyRecord`` rows for one org into
a lookup keyed by ``(scope, resource_id, kind)`` and answers
``resolve(kind, conversation_id=, user_id=, assistant_id=)`` by walking that order.
Pure logic — no DB, no clock — so it is cheap to invoke per-row inside the sweeper.

SaaS deployment default: 365 days for messages and events. Single-tenant: no
default until the customer seeds policies.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Mapping

from agent_runtime.persistence.records.retention import (
    RetentionKind,
    RetentionPolicyRecord,
    RetentionScope,
)


# 365 days. SaaS profiles (multi-tenant) get this default for messages
# and events; single-tenant deploys get None per the spec ("no default
# until customer sets policies").
_DEFAULT_TTL_SAAS = 365 * 24 * 60 * 60


DEPLOYMENT_DEFAULT_TTL_SECONDS: Mapping[RetentionKind, int | None] = {
    RetentionKind.MESSAGES: _DEFAULT_TTL_SAAS,
    RetentionKind.EVENTS: _DEFAULT_TTL_SAAS,
    RetentionKind.CONTEXT_PAYLOADS: None,
    RetentionKind.CHECKPOINTS: None,
    RetentionKind.MEMORY_ITEMS: None,
}


@dataclass(frozen=True)
class ResolvedPolicy:
    """The TTL that applies to one (kind, target) pair."""

    kind: RetentionKind
    ttl_seconds: int | None
    source_scope: RetentionScope | None  # None when ttl came from deployment default


class RetentionPolicyResolver:
    """In-memory resolver scoped to one ``org_id`` worth of policies."""

    def __init__(
        self,
        *,
        org_id: str,
        policies: Sequence[RetentionPolicyRecord],
        deployment_defaults: Mapping[RetentionKind, int | None] | None = None,
        privacy_user_retention_days: Mapping[str, int] | None = None,
    ) -> None:
        """Compose retention policies for one org.

        ``privacy_user_retention_days`` layers per-user
        ``privacy_settings.retention_days`` overrides on top of the org policy
        table. Each entry is synthesised as a USER-scope policy across every
        ``RetentionKind`` so the existing specificity walk picks it up unchanged.
        A user override applies to all retention kinds in lock-step (messages,
        events, context_payloads, checkpoints, memory_items), matching the
        "delete my chats after N days" user-visible promise. Explicit org/ops
        policies always win when they exist for the same (user, kind).
        """

        self._org_id = org_id
        self._defaults = (
            deployment_defaults
            if deployment_defaults is not None
            else DEPLOYMENT_DEFAULT_TTL_SECONDS
        )
        self._by_key: dict[
            tuple[RetentionScope, str, RetentionKind], RetentionPolicyRecord
        ] = {}
        for policy in policies:
            if policy.org_id != org_id:
                continue
            key = (policy.scope, policy.resource_id or "", policy.kind)
            self._by_key[key] = policy
        # Synthesize USER-scope rows from the privacy snapshot. Explicit org/ops
        # policy rows win when they exist for the same (user, kind).
        for user_id, retention_days in (privacy_user_retention_days or {}).items():
            if retention_days <= 0:
                continue
            ttl_seconds = retention_days * 24 * 60 * 60
            for kind in RetentionKind:
                key = (RetentionScope.USER, user_id, kind)
                if key in self._by_key:
                    continue
                self._by_key[key] = RetentionPolicyRecord(
                    org_id=org_id,
                    scope=RetentionScope.USER,
                    resource_id=user_id,
                    kind=kind,
                    ttl_seconds=ttl_seconds,
                )

    def resolve(
        self,
        *,
        kind: RetentionKind,
        conversation_id: str | None = None,
        user_id: str | None = None,
        assistant_id: str | None = None,
    ) -> ResolvedPolicy:
        """Walk specificity order; return the first hit, else the default."""

        order: tuple[tuple[RetentionScope, str | None], ...] = (
            (RetentionScope.CONVERSATION, conversation_id),
            (RetentionScope.ASSISTANT, assistant_id),
            (RetentionScope.USER, user_id),
            (RetentionScope.ORG, None),
        )
        for scope, resource_id in order:
            if scope != RetentionScope.ORG and not resource_id:
                continue
            key = (scope, resource_id or "", kind)
            policy = self._by_key.get(key)
            if policy is not None:
                return ResolvedPolicy(
                    kind=kind,
                    ttl_seconds=policy.ttl_seconds,
                    source_scope=scope,
                )
        return ResolvedPolicy(
            kind=kind,
            ttl_seconds=self._defaults.get(kind),
            source_scope=None,
        )

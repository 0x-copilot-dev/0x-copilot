"""C8 unit tests: most-specific policy wins; deployment defaults backstop."""

from __future__ import annotations

from agent_runtime.persistence.records.retention import (
    RetentionKind,
    RetentionPolicyRecord,
    RetentionScope,
)
from agent_runtime.retention import (
    DEPLOYMENT_DEFAULT_TTL_SECONDS,
    RetentionPolicyResolver,
)


def _policy(
    *,
    org_id: str = "org_a",
    scope: RetentionScope,
    resource_id: str | None,
    kind: RetentionKind,
    ttl_seconds: int,
) -> RetentionPolicyRecord:
    return RetentionPolicyRecord(
        org_id=org_id,
        scope=scope,
        resource_id=resource_id,
        kind=kind,
        ttl_seconds=ttl_seconds,
    )


class TestSpecificity:
    def test_conversation_beats_user_beats_org(self) -> None:
        policies = (
            _policy(
                scope=RetentionScope.ORG,
                resource_id=None,
                kind=RetentionKind.MESSAGES,
                ttl_seconds=3600,
            ),
            _policy(
                scope=RetentionScope.USER,
                resource_id="user_1",
                kind=RetentionKind.MESSAGES,
                ttl_seconds=1800,
            ),
            _policy(
                scope=RetentionScope.CONVERSATION,
                resource_id="conv_1",
                kind=RetentionKind.MESSAGES,
                ttl_seconds=600,
            ),
        )
        resolver = RetentionPolicyResolver(org_id="org_a", policies=policies)
        resolved = resolver.resolve(
            kind=RetentionKind.MESSAGES,
            conversation_id="conv_1",
            user_id="user_1",
        )
        assert resolved.ttl_seconds == 600
        assert resolved.source_scope is RetentionScope.CONVERSATION

    def test_user_used_when_no_conversation_policy(self) -> None:
        policies = (
            _policy(
                scope=RetentionScope.USER,
                resource_id="user_1",
                kind=RetentionKind.MESSAGES,
                ttl_seconds=1800,
            ),
        )
        resolver = RetentionPolicyResolver(org_id="org_a", policies=policies)
        resolved = resolver.resolve(
            kind=RetentionKind.MESSAGES,
            conversation_id="conv_999",
            user_id="user_1",
        )
        assert resolved.ttl_seconds == 1800
        assert resolved.source_scope is RetentionScope.USER

    def test_org_default_when_no_specific_policy(self) -> None:
        resolver = RetentionPolicyResolver(org_id="org_a", policies=())
        resolved = resolver.resolve(kind=RetentionKind.MESSAGES)
        # SaaS default = 365d.
        assert (
            resolved.ttl_seconds
            == DEPLOYMENT_DEFAULT_TTL_SECONDS[RetentionKind.MESSAGES]
        )
        assert resolved.source_scope is None

    def test_no_default_for_unmapped_kind(self) -> None:
        resolver = RetentionPolicyResolver(
            org_id="org_a",
            policies=(),
            deployment_defaults={RetentionKind.MESSAGES: None},
        )
        resolved = resolver.resolve(kind=RetentionKind.MESSAGES)
        assert resolved.ttl_seconds is None
        assert resolved.source_scope is None


class TestTenantIsolation:
    def test_other_org_policies_ignored(self) -> None:
        policies = (
            _policy(
                org_id="org_b",
                scope=RetentionScope.ORG,
                resource_id=None,
                kind=RetentionKind.MESSAGES,
                ttl_seconds=60,
            ),
        )
        resolver = RetentionPolicyResolver(org_id="org_a", policies=policies)
        resolved = resolver.resolve(kind=RetentionKind.MESSAGES)
        # Falls through to deployment default — never picks up org_b's policy.
        assert (
            resolved.ttl_seconds
            == DEPLOYMENT_DEFAULT_TTL_SECONDS[RetentionKind.MESSAGES]
        )

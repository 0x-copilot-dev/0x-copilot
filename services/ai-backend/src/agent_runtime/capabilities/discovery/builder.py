"""Deterministic catalog projection over existing authorized compact cards."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
import hashlib
import hmac
import json

from agent_runtime.capabilities.discovery.contracts import (
    ApprovalCue,
    CapabilityCatalog,
    CapabilityCatalogRevision,
    CapabilityCatalogScope,
    CapabilityIndexEntry,
    CapabilitySource,
)
from agent_runtime.capabilities.mcp.cards import McpServerCard
from agent_runtime.capabilities.mcp.permissions import McpPermissionPolicy
from agent_runtime.capabilities.tools.cards import ToolCard, ToolRiskLevel
from agent_runtime.capabilities.tools.permissions import ToolPermissionChecker
from agent_runtime.execution.contracts import AgentRuntimeContext

_REF_KEY_MIN_BYTES = 32


class AuthorizedCatalogBuilder:
    """Build a schema-free catalog and defensively recheck every compact card."""

    def __init__(self, *, reference_key: bytes) -> None:
        if len(reference_key) < _REF_KEY_MIN_BYTES:
            msg = f"reference_key must contain at least {_REF_KEY_MIN_BYTES} bytes"
            raise ValueError(msg)
        self._reference_key = bytes(reference_key)

    def build(
        self,
        *,
        context: AgentRuntimeContext,
        scope: CapabilityCatalogScope,
        tool_cards: Sequence[ToolCard] = (),
        mcp_server_cards: Sequence[McpServerCard] = (),
        deferred_schema_tokens: int = 0,
        expires_at: datetime,
    ) -> CapabilityCatalog:
        """Project only cards visible under the supplied verified run context."""

        if not scope.matches(context):
            msg = "catalog scope does not match the runtime context"
            raise ValueError(msg)
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            msg = "expires_at must be timezone-aware"
            raise ValueError(msg)
        if deferred_schema_tokens < 0:
            msg = "deferred_schema_tokens must be non-negative"
            raise ValueError(msg)

        catalog_id = self._opaque_id("cat", self._scope_identity(scope))
        entries = [
            self._tool_entry(
                catalog_id=catalog_id,
                card=card,
            )
            for card in tool_cards
            if ToolPermissionChecker.is_card_authorized(context, card)
        ]
        entries.extend(
            self._mcp_server_entry(
                catalog_id=catalog_id,
                card=card,
            )
            for card in mcp_server_cards
            if McpPermissionPolicy.is_server_card_visible(context, card)
        )
        entries.sort(
            key=lambda entry: (
                entry.source.value,
                entry.stable_name,
                entry.connector_label,
                entry.capability_ref,
            )
        )
        self._reject_duplicate_identities(entries)

        revision = self._revision(entries)
        return CapabilityCatalog(
            scope=scope,
            revision=CapabilityCatalogRevision(
                catalog_id=catalog_id,
                revision=revision,
                profile_id=scope.profile_id,
                user_id=scope.user_id,
                policy_revision=scope.policy_revision,
                connector_scope_revision=scope.connector_scope_revision,
                descriptor_count=len(entries),
                deferred_schema_tokens=deferred_schema_tokens,
                expires_at=expires_at,
            ),
            entries=tuple(entries),
        )

    def _tool_entry(
        self,
        *,
        catalog_id: str,
        card: ToolCard,
    ) -> CapabilityIndexEntry:
        identity = f"{CapabilitySource.TOOL_CARD.value}:{card.connector}:{card.name}"
        approval_cue = (
            ApprovalCue.POLICY_DEPENDENT
            if card.risk_level in {ToolRiskLevel.HIGH, ToolRiskLevel.CRITICAL}
            else ApprovalCue.UNKNOWN
        )
        return CapabilityIndexEntry(
            capability_ref=self._opaque_id("cap", f"{catalog_id}:{identity}"),
            source=CapabilitySource.TOOL_CARD,
            stable_name=card.name,
            display_name=card.display_name,
            concise_description=card.short_description,
            intent_tags=tuple(card.tags),
            connector_label=card.connector,
            approval_cue=approval_cue,
        )

    def _mcp_server_entry(
        self,
        *,
        catalog_id: str,
        card: McpServerCard,
    ) -> CapabilityIndexEntry:
        source_id = card.server_id or card.name
        identity = f"{CapabilitySource.MCP_SERVER.value}:{source_id}:{card.name}"
        return CapabilityIndexEntry(
            capability_ref=self._opaque_id("cap", f"{catalog_id}:{identity}"),
            source=CapabilitySource.MCP_SERVER,
            stable_name=card.name,
            display_name=card.display_name or card.name,
            concise_description=card.short_description,
            connector_label=card.connector_slug or card.display_name or card.name,
        )

    @staticmethod
    def _scope_identity(scope: CapabilityCatalogScope) -> str:
        return json.dumps(
            scope.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )

    def _opaque_id(self, prefix: str, identity: str) -> str:
        digest = hmac.new(
            self._reference_key,
            identity.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:32]
        return f"{prefix}_{digest}"

    @staticmethod
    def _revision(entries: Sequence[CapabilityIndexEntry]) -> str:
        encoded = json.dumps(
            [entry.model_dump(mode="json") for entry in entries],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return f"rev_{hashlib.sha256(encoded).hexdigest()[:32]}"

    @staticmethod
    def _reject_duplicate_identities(
        entries: Sequence[CapabilityIndexEntry],
    ) -> None:
        identities = [
            (entry.source, entry.stable_name, entry.connector_label)
            for entry in entries
        ]
        if len(identities) != len(set(identities)):
            msg = "catalog contains duplicate compact-card identities"
            raise ValueError(msg)

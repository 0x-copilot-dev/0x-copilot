"""Deterministic catalog projection over existing authorized compact cards."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
import hashlib
import json

from agent_runtime.capabilities.discovery.contracts import (
    ApprovalCue,
    CapabilityBridgeToolName,
    CapabilityCatalog,
    CapabilityCatalogGeneration,
    CapabilityCatalogRevision,
    CapabilityCatalogScope,
    CapabilityIndexEntry,
    CapabilityReferenceFormat,
    CapabilitySource,
    CapabilitySubjectFingerprint,
    CatalogDescriptorRevision,
    HmacCapabilityReferenceMinter,
)
from agent_runtime.capabilities.mcp.cards import McpServerCard
from agent_runtime.capabilities.mcp.permissions import McpPermissionPolicy
from agent_runtime.capabilities.tools.cards import ToolCard, ToolRiskLevel
from agent_runtime.capabilities.tools.permissions import ToolPermissionChecker
from agent_runtime.execution.contracts import AgentRuntimeContext


class AuthorizedCatalogBuilder:
    """Build a schema-free catalog and defensively recheck every compact card."""

    def __init__(self, *, reference_key: bytes) -> None:
        # Both derivations go through the one reference minter, so opaque refs
        # and subject fingerprints can never be produced from different
        # key-strength bars, and the ref the builder mints is byte-identical to
        # the one the second-tier expander mints from the same key.
        self._minter = HmacCapabilityReferenceMinter(reference_key=reference_key)
        self._subject_fingerprint = CapabilitySubjectFingerprint(
            reference_key=reference_key
        )

    def subject_fingerprint(self, context: AgentRuntimeContext) -> str:
        """Return the same subject fingerprint this builder keys generations to.

        Use-time revalidation has to present the verified subject in exactly the
        representation the minted reference was bound to; exposing the one
        derivation keeps that from being reimplemented at a call site.
        """

        return self._subject_fingerprint.derive(context)

    def build(
        self,
        *,
        context: AgentRuntimeContext,
        scope: CapabilityCatalogScope,
        task_policy_selection_ref: str,
        tool_cards: Sequence[ToolCard] = (),
        mcp_server_cards: Sequence[McpServerCard] = (),
        descriptor_revisions: Sequence[CatalogDescriptorRevision] = (),
        deferred_schema_tokens: int = 0,
        expires_at: datetime,
    ) -> CapabilityCatalog:
        """Project only cards visible under the supplied verified run context.

        The catalog is always stamped with the generation identity of the four
        trusted inputs it was projected from — the verified subject, the
        connector scope revision, the F4 task-policy selection, and the folded
        F8 descriptor revisions — so every ref it mints is bindable and can be
        revalidated against live authority at use time.
        """

        if not scope.matches(context):
            msg = "catalog scope does not match the runtime context"
            raise ValueError(msg)
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            msg = "expires_at must be timezone-aware"
            raise ValueError(msg)
        if deferred_schema_tokens < 0:
            msg = "deferred_schema_tokens must be non-negative"
            raise ValueError(msg)

        catalog_id = self._minter.mint_catalog_id(
            scope_identity=self._scope_identity(scope)
        )
        entries = [
            self._tool_entry(
                catalog_id=catalog_id,
                card=card,
            )
            for card in tool_cards
            if ToolPermissionChecker.is_card_authorized(context, card)
            and not self._claims_a_bridge_name(card.name)
        ]
        entries.extend(
            self._mcp_server_entry(
                catalog_id=catalog_id,
                card=card,
            )
            for card in mcp_server_cards
            if McpPermissionPolicy.is_server_card_visible(context, card)
            and not self._claims_a_bridge_name(card.name)
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
                generation=CapabilityCatalogGeneration.create(
                    subject_fingerprint=self.subject_fingerprint(context),
                    connector_scope_revision=scope.connector_scope_revision,
                    task_policy_selection_ref=task_policy_selection_ref,
                    descriptor_revisions=descriptor_revisions,
                ),
            ),
            entries=tuple(entries),
        )

    @staticmethod
    def _claims_a_bridge_name(name: str) -> bool:
        """Drop a compact card that would collide with a bridge tool name.

        Excluding the card narrows: the capability simply stays undiscoverable
        through F3 while the pre-F3 direct/server disclosure is untouched.
        Refusing the whole catalog instead would let one misconfigured card fail
        an entire run, and admitting it is the one thing that could make a
        bridge tool reachable from a bridge tool.
        """

        return CapabilityBridgeToolName.is_reserved(name)

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
            capability_ref=self._minter.mint(
                catalog_id=catalog_id,
                identity=identity,
            ),
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
            capability_ref=self._minter.mint(
                catalog_id=catalog_id,
                identity=identity,
            ),
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

    @staticmethod
    def _revision(entries: Sequence[CapabilityIndexEntry]) -> str:
        """Digest the projected catalog *content*, unkeyed and reproducible.

        This is deliberately not the keyed minter: a revision must be
        recomputable from the catalog body alone by anyone holding the body, so
        two builds of identical content compare equal without the secret.
        """

        encoded = json.dumps(
            [entry.model_dump(mode="json") for entry in entries],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        prefix = CapabilityReferenceFormat.REVISION_PREFIX
        return f"{prefix}_{digest[: CapabilityReferenceFormat.HEX_CHARS]}"

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

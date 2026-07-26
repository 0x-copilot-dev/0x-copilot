"""Worker material resolution kept outside the provider transport adapter."""

from __future__ import annotations

import json
from dataclasses import dataclass

from agent_runtime.capabilities.mcp.effect_material import McpEffectMaterial
from agent_runtime.capabilities.mcp.target_ref import McpTargetRefCodec
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes, sha256_hex
from agent_runtime.surfaces_v2.ledger_ids import OperationArgsRefCodec


@dataclass(frozen=True)
class McpOperationArgumentMaterialResolver:
    """Resolve immutable MCP material for the worker-owned effect executor."""

    arguments: object
    additional_material_resolvers: tuple[object, ...] = ()

    async def resolve(self, request: object) -> object | None:
        """Return validated material without giving that resolver to a tool adapter."""

        for resolver in self.additional_material_resolvers:
            resolve = getattr(resolver, "resolve", None)
            if resolve is None:
                continue
            material = await resolve(request)
            if material is not None:
                return material

        proposal_content_ref = getattr(request, "proposal_content_ref", None)
        proposal_digest = getattr(request, "proposal_digest", None)
        target_ref = getattr(request, "target_ref", None)
        if not all(
            isinstance(value, str)
            for value in (proposal_content_ref, proposal_digest, target_ref)
        ):
            return None
        try:
            OperationArgsRefCodec.parse(proposal_content_ref)
            target = McpTargetRefCodec.parse(target_ref)
            resolve = getattr(self.arguments, "resolve")
            canonical_bytes = await resolve(
                ref=proposal_content_ref,
                digest=proposal_digest,
            )
            if (
                canonical_bytes is None
                or sha256_hex(canonical_bytes) != proposal_digest
            ):
                return None
            decoded = json.loads(canonical_bytes)
            if (
                not isinstance(decoded, dict)
                or canonical_json_bytes(decoded) != canonical_bytes
            ):
                return None
            return McpEffectMaterial(
                target_connector=target.capability,
                target_op=target.op,
                arguments=decoded,
                target_ref=target_ref,
                target_digest=getattr(request, "target_digest"),
                proposal_ref=getattr(request, "proposal_ref"),
                proposal_content_ref=proposal_content_ref,
                proposal_digest=proposal_digest,
            )
        except Exception:
            return None


__all__ = ("McpOperationArgumentMaterialResolver",)

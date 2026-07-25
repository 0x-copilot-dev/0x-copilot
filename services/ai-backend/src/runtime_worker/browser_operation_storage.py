"""Durable exact-plan material for the desktop browser A4/A5 path.

The operation adapter and the commit worker construct separate instances over
the same content-addressed blob/reference stores. No process-local map is
authority: an approved browser submission remains executable after a worker
restart only when both its canonical plan and canonical target bytes still
exist under user/org-scoped EFFECT references.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from urllib.parse import urlsplit

from agent_runtime.artifacts.ports import ArtifactBlobStorePort
from agent_runtime.capabilities.browser.contracts import (
    BrowserActionPlan,
    BrowserStoredPlan,
)
from agent_runtime.capabilities.browser.effect_adapter import (
    browser_target_material,
    browser_target_ref,
)
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes, sha256_hex
from runtime_adapters.artifact_references import (
    ArtifactReferenceEdge,
    ArtifactReferenceKind,
    ArtifactReferenceRepositoryPort,
)

MAX_BROWSER_PLAN_BYTES = 1_048_576
_PLAN_SCHEME = "browser-plan"
_TARGET_SCHEME = "browser-target"


async def _one_chunk(body: bytes) -> AsyncIterator[bytes]:
    yield body


async def _read_bounded(stream: AsyncIterator[bytes], *, limit: int) -> bytes | None:
    body = bytearray()
    async for chunk in stream:
        if not isinstance(chunk, bytes) or len(body) + len(chunk) > limit:
            return None
        body.extend(chunk)
    return bytes(body)


@dataclass(frozen=True)
class RuntimeBrowserActionPlanStore:
    """Persist/load browser plans and expose A5's immutable reference bytes."""

    blobs: ArtifactBlobStorePort
    references: ArtifactReferenceRepositoryPort
    org_id: str
    user_id: str

    async def store(self, *, plan: BrowserActionPlan) -> BrowserStoredPlan:
        plan_body = canonical_json_bytes(plan.model_dump(mode="json"))
        plan_digest = sha256_hex(plan_body)
        if plan_digest != plan.digest or len(plan_body) > MAX_BROWSER_PLAN_BYTES:
            raise ValueError("browser action plan is not canonical")
        content_ref = f"{_PLAN_SCHEME}://{plan_digest}"
        await self._retain(
            reference=content_ref,
            digest=plan_digest,
            body=plan_body,
        )

        target_body = browser_target_material(plan)
        target_digest = sha256_hex(target_body)
        if target_digest != plan.target_digest:
            raise ValueError("browser action target is not canonical")
        await self._retain(
            reference=browser_target_ref(plan),
            digest=target_digest,
            body=target_body,
        )
        return BrowserStoredPlan(content_ref=content_ref, digest=plan_digest)

    async def load(self, *, content_ref: str) -> BrowserActionPlan | None:
        expected_digest = _reference_digest(
            content_ref,
            expected_scheme=_PLAN_SCHEME,
        )
        if expected_digest is None:
            return None
        body = await self.open_reference(ref=content_ref)
        if body is None or sha256_hex(body) != expected_digest:
            return None
        try:
            plan = BrowserActionPlan.model_validate_json(body)
        except Exception:
            return None
        if (
            plan.digest != expected_digest
            or canonical_json_bytes(plan.model_dump(mode="json")) != body
        ):
            return None
        return plan

    async def open_reference(self, *, ref: str) -> bytes | None:
        """Open only an active user-scoped browser plan/target reference."""

        parsed = urlsplit(ref)
        if parsed.scheme not in {_PLAN_SCHEME, _TARGET_SCHEME}:
            return None
        if (
            parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or len(parsed.netloc) != 64
        ):
            return None
        edges = await self.references.list_edges(
            org_id=self.org_id,
            user_id=self.user_id,
        )
        edge = next(
            (
                candidate
                for candidate in edges
                if candidate.reference_kind is ArtifactReferenceKind.EFFECT
                and candidate.reference_id == ref
                and candidate.user_id == self.user_id
                and candidate.released_at is None
            ),
            None,
        )
        if edge is None:
            return None
        body = await _read_bounded(
            await self.blobs.open_stream(edge.blob_key),
            limit=MAX_BROWSER_PLAN_BYTES,
        )
        if body is None or sha256_hex(body) != edge.blob_key:
            return None
        return body

    async def _retain(self, *, reference: str, digest: str, body: bytes) -> None:
        if len(body) > MAX_BROWSER_PLAN_BYTES or sha256_hex(body) != digest:
            raise ValueError("browser effect material is invalid")
        stored = await self.blobs.put_stream(
            expected_digest=digest,
            chunks=_one_chunk(body),
            byte_limit=MAX_BROWSER_PLAN_BYTES,
        )
        if stored.blob_key != digest:
            raise ValueError("browser effect material changed during storage")
        seed = f"{self.org_id}\0effect\0{reference}\0{digest}".encode()
        await self.references.acquire(
            ArtifactReferenceEdge(
                org_id=self.org_id,
                edge_id=f"effect-{hashlib.sha256(seed).hexdigest()}",
                user_id=self.user_id,
                blob_key=digest,
                reference_kind=ArtifactReferenceKind.EFFECT,
                reference_id=reference,
                created_at=datetime.now(timezone.utc),
            )
        )


def _reference_digest(reference: str, *, expected_scheme: str) -> str | None:
    parsed = urlsplit(reference)
    if (
        parsed.scheme != expected_scheme
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or len(parsed.netloc) != 64
        or any(char not in "0123456789abcdef" for char in parsed.netloc)
    ):
        return None
    return parsed.netloc


__all__ = ("RuntimeBrowserActionPlanStore",)

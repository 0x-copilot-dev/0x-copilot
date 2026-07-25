"""Durability and tenant-boundary tests for exact browser action plans."""

from __future__ import annotations

from agent_runtime.capabilities.browser.contracts import (
    BrowserActionKind,
    BrowserActionPlan,
    BrowserPrecondition,
)
from agent_runtime.capabilities.browser.effect_adapter import (
    browser_target_material,
    browser_target_ref,
)
from agent_runtime.surfaces_v2.canonical_json import sha256_hex
from runtime_adapters.artifact_references import InMemoryArtifactReferenceStore
from runtime_adapters.in_memory.artifact_blob_store import InMemoryArtifactBlobStore
from runtime_adapters.in_memory.artifact_publication import (
    InMemoryArtifactPublicationCoordinator,
)
from runtime_worker.browser_operation_storage import RuntimeBrowserActionPlanStore


def _plan() -> BrowserActionPlan:
    precondition = BrowserPrecondition(
        page_generation=4,
        origin="https://example.com",
        element_fingerprint="a" * 64,
    )
    return BrowserActionPlan(
        session_ref="browser-session://ses_exact",
        page_ref="browser-page://pg_exact",
        origin="https://example.com",
        top_level_origin="https://example.com",
        action_kind=BrowserActionKind.CLICK,
        element_ref="e4_2",
        element_fingerprint="a" * 64,
        canonical_fields_ref=(
            "operation://op_00000000-0000-4000-8000-000000000001/args"
        ),
        fields_digest="b" * 64,
        precondition=precondition,
        precondition_digest=precondition.digest,
        user_visible_summary="Review browser click on https://example.com.",
    )


def _stores() -> tuple[
    InMemoryArtifactBlobStore,
    InMemoryArtifactReferenceStore,
]:
    coordinator = InMemoryArtifactPublicationCoordinator()
    return (
        InMemoryArtifactBlobStore(coordinator),
        InMemoryArtifactReferenceStore(coordinator),
    )


async def test_plan_and_target_survive_a_fresh_store_instance() -> None:
    blobs, references = _stores()
    first = RuntimeBrowserActionPlanStore(
        blobs=blobs,
        references=references,
        org_id="org-1",
        user_id="user-1",
    )
    stored = await first.store(plan=_plan())
    reopened = RuntimeBrowserActionPlanStore(
        blobs=blobs,
        references=references,
        org_id="org-1",
        user_id="user-1",
    )

    loaded = await reopened.load(content_ref=stored.content_ref)
    target = await reopened.open_reference(ref=browser_target_ref(_plan()))

    assert loaded == _plan()
    assert target == browser_target_material(_plan())
    assert sha256_hex(target) == _plan().target_digest


async def test_foreign_user_cannot_open_plan_or_target() -> None:
    blobs, references = _stores()
    owner = RuntimeBrowserActionPlanStore(
        blobs=blobs,
        references=references,
        org_id="org-1",
        user_id="user-1",
    )
    stored = await owner.store(plan=_plan())
    foreign = RuntimeBrowserActionPlanStore(
        blobs=blobs,
        references=references,
        org_id="org-1",
        user_id="user-2",
    )

    assert await foreign.load(content_ref=stored.content_ref) is None
    assert await foreign.open_reference(ref=browser_target_ref(_plan())) is None


async def test_unrecognized_or_digest_mismatched_refs_fail_closed() -> None:
    blobs, references = _stores()
    store = RuntimeBrowserActionPlanStore(
        blobs=blobs,
        references=references,
        org_id="org-1",
        user_id="user-1",
    )
    await store.store(plan=_plan())

    assert await store.load(content_ref="operation://not-a-plan/args") is None
    assert await store.load(content_ref=f"browser-plan://{'0' * 64}") is None
    assert await store.open_reference(ref=f"browser-target://{'0' * 64}") is None

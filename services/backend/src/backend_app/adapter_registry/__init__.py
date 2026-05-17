"""Tier-2 adapter registry (Phase 7A).

Server-side storage, review queue, and promotion lifecycle for
agent-generated `SaaSRendererAdapter` implementations. The desktop
client harvests local adapters that meet the §9.5.3 success criteria
and submits them here; admins review; approved adapters propagate to
every tenant that has not opted out.

Module layout mirrors the existing backend service modules
(``notifications``, ``policies``, ``privacy``):

* ``models``          — Pydantic v2 wire + record types.
* ``storage``         — content-addressed object-store port + dev impl.
* ``store``           — In-memory + Postgres adapters for the Postgres rows.
* ``registry_service`` — domain orchestration on top of store + storage.
* ``routes``          — FastAPI router (mounted under ``/internal/v1/adapter_registry``).
"""

from __future__ import annotations

from backend_app.adapter_registry.models import (
    AdapterCandidateRecord,
    AdapterCandidateStatus,
    AdapterCandidateSubmission,
    AdapterCandidateView,
    AdapterCandidateListResponse,
    AdapterRegistryOptOutRequest,
    AdapterRegistryOptOutResponse,
    AdapterReviewAction,
    AdapterReviewDecisionRequest,
    AdapterReviewRecord,
    AdapterRegistryAuditEventRecord,
    PromotedAdapterRecord,
    PromotedAdapterView,
    PromotedAdaptersResponse,
    TenantAdapterSettingsRecord,
)
from backend_app.adapter_registry.registry_service import AdapterRegistryService
from backend_app.adapter_registry.routes import register_adapter_registry_routes
from backend_app.adapter_registry.storage import (
    LocalFilesystemSourceStorage,
    SourceStorage,
    StoredSource,
)
from backend_app.adapter_registry.store import (
    AdapterRegistryStore,
    InMemoryAdapterRegistryStore,
    PostgresAdapterRegistryStore,
)


__all__ = [
    "AdapterCandidateListResponse",
    "AdapterCandidateRecord",
    "AdapterCandidateStatus",
    "AdapterCandidateSubmission",
    "AdapterCandidateView",
    "AdapterRegistryAuditEventRecord",
    "AdapterRegistryOptOutRequest",
    "AdapterRegistryOptOutResponse",
    "AdapterRegistryService",
    "AdapterRegistryStore",
    "AdapterReviewAction",
    "AdapterReviewDecisionRequest",
    "AdapterReviewRecord",
    "InMemoryAdapterRegistryStore",
    "LocalFilesystemSourceStorage",
    "PostgresAdapterRegistryStore",
    "PromotedAdapterRecord",
    "PromotedAdapterView",
    "PromotedAdaptersResponse",
    "SourceStorage",
    "StoredSource",
    "TenantAdapterSettingsRecord",
    "register_adapter_registry_routes",
]

"""Per-model-call context planning: candidate supply, allocation, and plans.

The contracts these modules bind to live in
:mod:`agent_runtime.context.context_contracts`; the material behind the
references they carry is read through
:mod:`agent_runtime.context.evidence_registry`.  Nothing here holds a body.
"""

from __future__ import annotations

from agent_runtime.context.planning.providers import (
    ContextCandidateCollection,
    ContextCandidateCollector,
    ContextCandidateIdentity,
    ContextCandidateRequest,
    ContextCollectionRejected,
    ContextProviderAlreadyRegistered,
    ContextProviderBounds,
    ContextProviderError,
    ContextProviderNotConfigured,
    ContextProviderOffer,
    ContextProviderOutcome,
    ContextProviderPolicies,
    ContextProviderReport,
    ContextProviderReportRejected,
    ContextProviderTables,
    ContextSourceAuthorityPort,
    ContextSourceEnumerationPort,
    ContextSourcePolicy,
    ContextSourcePolicyRejected,
    ContextSourceRecord,
    ContextWithholdingTally,
    ScopedCandidateProvider,
)

__all__ = (
    "ContextCandidateCollection",
    "ContextCandidateCollector",
    "ContextCandidateIdentity",
    "ContextCandidateRequest",
    "ContextCollectionRejected",
    "ContextProviderAlreadyRegistered",
    "ContextProviderBounds",
    "ContextProviderError",
    "ContextProviderNotConfigured",
    "ContextProviderOffer",
    "ContextProviderOutcome",
    "ContextProviderPolicies",
    "ContextProviderReport",
    "ContextProviderReportRejected",
    "ContextProviderTables",
    "ContextSourceAuthorityPort",
    "ContextSourceEnumerationPort",
    "ContextSourcePolicy",
    "ContextSourcePolicyRejected",
    "ContextSourceRecord",
    "ContextWithholdingTally",
    "ScopedCandidateProvider",
)

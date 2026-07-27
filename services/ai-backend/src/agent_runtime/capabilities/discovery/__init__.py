"""Policy-aware compact capability discovery."""

from agent_runtime.capabilities.discovery.builder import AuthorizedCatalogBuilder
from agent_runtime.capabilities.discovery.contracts import (
    ApprovalCue,
    CapabilityCandidate,
    CapabilityCatalog,
    CapabilityCatalogRevision,
    CapabilityCatalogScope,
    CapabilityIndexEntry,
    CapabilitySearchFilters,
    CapabilitySearchRequest,
    CapabilitySearchResult,
    CapabilitySource,
    CatalogEffectClass,
)
from agent_runtime.capabilities.discovery.ranker import DeterministicLexicalRanker

__all__ = [
    "ApprovalCue",
    "AuthorizedCatalogBuilder",
    "CapabilityCandidate",
    "CapabilityCatalog",
    "CapabilityCatalogRevision",
    "CapabilityCatalogScope",
    "CapabilityIndexEntry",
    "CapabilitySearchFilters",
    "CapabilitySearchRequest",
    "CapabilitySearchResult",
    "CapabilitySource",
    "CatalogEffectClass",
    "DeterministicLexicalRanker",
]

"""Policy-aware compact capability discovery."""

from agent_runtime.capabilities.discovery.builder import AuthorizedCatalogBuilder
from agent_runtime.capabilities.discovery.contracts import (
    ApprovalCue,
    CapabilityCandidate,
    CapabilityCatalog,
    CapabilityDescribeRequest,
    CapabilityDescribeResult,
    CapabilityDescribeToolResult,
    CapabilityDescription,
    CapabilityDiscoveryError,
    CapabilityDiscoveryErrorCode,
    CapabilityCatalogRevision,
    CapabilityCatalogScope,
    CapabilityIndexEntry,
    CapabilityParameterHint,
    CapabilitySearchFilters,
    CapabilitySearchRequest,
    CapabilitySearchResult,
    CapabilitySearchToolResult,
    CapabilitySource,
    CatalogEffectClass,
)
from agent_runtime.capabilities.discovery.ranker import DeterministicLexicalRanker
from agent_runtime.capabilities.discovery.tool_bridge import (
    CapabilityCatalogAccess,
    CapabilityDescribeTool,
    CapabilitySearchTool,
)

__all__ = [
    "ApprovalCue",
    "AuthorizedCatalogBuilder",
    "CapabilityCandidate",
    "CapabilityCatalog",
    "CapabilityCatalogAccess",
    "CapabilityCatalogRevision",
    "CapabilityCatalogScope",
    "CapabilityDescribeRequest",
    "CapabilityDescribeResult",
    "CapabilityDescribeTool",
    "CapabilityDescribeToolResult",
    "CapabilityDescription",
    "CapabilityDiscoveryError",
    "CapabilityDiscoveryErrorCode",
    "CapabilityIndexEntry",
    "CapabilityParameterHint",
    "CapabilitySearchFilters",
    "CapabilitySearchRequest",
    "CapabilitySearchResult",
    "CapabilitySearchTool",
    "CapabilitySearchToolResult",
    "CapabilitySource",
    "CatalogEffectClass",
    "DeterministicLexicalRanker",
]

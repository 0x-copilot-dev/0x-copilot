"""Universal, descriptor-driven operation normalization (Generative Surfaces v2.1)."""

from agent_runtime.capabilities.operations.classifier import OperationClassifier
from agent_runtime.capabilities.operations.builtin_catalog import (
    BuiltinOperationCatalog,
    BuiltinOperationCatalogEntry,
    BuiltinOperationExecution,
    BuiltinOperationKind,
    DEFAULT_BUILTIN_OPERATION_CATALOG,
)
from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.operations.contracts import (
    GateResolution,
    OperationAdapter,
    OperationClassification,
    OperationGatewayMode,
    OperationOutcomePresenter,
    OperationPresentationOutcome,
    OperationRawResult,
    OperationResultSummary,
    PresentationPlan,
    ProposedEffect,
)
from agent_runtime.capabilities.operations.descriptors import (
    OperationDescriptorEntry,
    OperationDescriptorRegistry,
)
from agent_runtime.capabilities.operations.disposition import (
    PresentationDispositionPolicy,
)
from agent_runtime.capabilities.operations.gateway import OperationGateway
from agent_runtime.capabilities.operations.tree import (
    OperationNode,
    OperationNodeStatus,
    OperationTree,
    OperationTreeEvent,
    OperationTreeProjection,
    OperationUsageRecord,
    OperationUsageTotals,
)

__all__ = (
    "GateResolution",
    "BuiltinOperationCatalog",
    "BuiltinOperationCatalogEntry",
    "BuiltinOperationExecution",
    "BuiltinOperationKind",
    "DEFAULT_BUILTIN_OPERATION_CATALOG",
    "OperationAdapter",
    "OperationClassification",
    "OperationClassifier",
    "OperationContext",
    "OperationDescriptorEntry",
    "OperationDescriptorRegistry",
    "OperationGateway",
    "OperationGatewayMode",
    "OperationOutcomePresenter",
    "OperationPresentationOutcome",
    "OperationRawResult",
    "OperationResultSummary",
    "OperationNode",
    "OperationNodeStatus",
    "OperationTree",
    "OperationTreeEvent",
    "OperationTreeProjection",
    "OperationUsageRecord",
    "OperationUsageTotals",
    "PresentationDispositionPolicy",
    "PresentationPlan",
    "ProposedEffect",
)

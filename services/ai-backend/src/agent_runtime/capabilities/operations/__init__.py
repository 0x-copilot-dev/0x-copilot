"""Universal, descriptor-driven operation normalization (Generative Surfaces v2.1)."""

from agent_runtime.capabilities.operations.classifier import OperationClassifier
from agent_runtime.capabilities.operations.context import OperationContext
from agent_runtime.capabilities.operations.contracts import (
    GateResolution,
    OperationAdapter,
    OperationClassification,
    OperationGatewayMode,
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

__all__ = (
    "GateResolution",
    "OperationAdapter",
    "OperationClassification",
    "OperationClassifier",
    "OperationContext",
    "OperationDescriptorEntry",
    "OperationDescriptorRegistry",
    "OperationGateway",
    "OperationGatewayMode",
    "OperationRawResult",
    "OperationResultSummary",
    "PresentationDispositionPolicy",
    "PresentationPlan",
    "ProposedEffect",
)

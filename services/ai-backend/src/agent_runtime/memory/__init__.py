"""Context and memory management primitives for the AI backend."""

from agent_runtime.memory.backends import (
    MemoryBackendRoute,
    MemoryFileSnapshot,
    MemoryRoutePlan,
    ScopedMemoryBackendFactory,
    VersionedMemoryStore,
)
from agent_runtime.memory.contracts import (
    ContextCompressionEvent,
    ContextCompressionStrategy,
    ContextFallbackTrigger,
    ContextSummary,
    ManagedContextPayload,
    MemoryAccessOperation,
    MemoryActorRole,
    MemoryPathPolicy,
    MemoryScope,
    MemoryScopeType,
    TokenBudgetPolicy,
)
from agent_runtime.memory.policy import MemoryPolicyAuthorizer
from agent_runtime.memory.summarization import (
    ContextPayloadManager,
    ContextSummarizationManager,
    SummarizationResult,
)
from agent_runtime.memory.token_budget import TokenBudgetEvaluator, TokenBudgetSnapshot

__all__ = [
    "ContextCompressionEvent",
    "ContextCompressionStrategy",
    "ContextFallbackTrigger",
    "ContextPayloadManager",
    "ContextSummarizationManager",
    "ContextSummary",
    "ManagedContextPayload",
    "MemoryAccessOperation",
    "MemoryActorRole",
    "MemoryBackendRoute",
    "MemoryFileSnapshot",
    "MemoryPathPolicy",
    "MemoryPolicyAuthorizer",
    "MemoryRoutePlan",
    "MemoryScope",
    "MemoryScopeType",
    "ScopedMemoryBackendFactory",
    "SummarizationResult",
    "TokenBudgetEvaluator",
    "TokenBudgetPolicy",
    "TokenBudgetSnapshot",
    "VersionedMemoryStore",
]

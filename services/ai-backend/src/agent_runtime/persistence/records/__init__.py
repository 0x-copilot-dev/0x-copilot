"""Durable persistence records grouped by aggregate."""

from agent_runtime.persistence.records.approvals import PersistenceApprovalRequestRecord
from agent_runtime.persistence.records.audit import AuditLogRecord
from agent_runtime.persistence.records.budgets import (
    BudgetEnforcement,
    BudgetPeriod,
    BudgetRecord,
    BudgetReservationRecord,
    BudgetScope,
    BudgetStateRecord,
    BudgetStatus,
    BudgetWithState,
    ChargeOutcome,
)
from agent_runtime.persistence.records.checkpoints import CheckpointRecord
from agent_runtime.persistence.records.common import (
    ApprovalRiskClass,
    AsyncTaskStatus,
    AuditActorType,
    AuditOutcome,
    OutboxStatus,
    PayloadKind,
    PayloadRedactionState,
    PayloadStorageBackend,
    PersistenceApprovalStatus,
    PersistenceValueNormalizer,
    RuntimeMemoryScopeType,
    ToolInvocationStatus,
    ToolSideEffectClass,
)
from agent_runtime.persistence.records.memory import MemoryItemRecord, MemoryScopeRecord
from agent_runtime.persistence.records.outbox import (
    ConsumerCursorRecord,
    OutboxEventRecord,
    RuntimeWorkerClaim,
    RuntimeWorkerResult,
)
from agent_runtime.persistence.records.payloads import ContextPayloadRecord
from agent_runtime.persistence.records.retention import (
    RetentionKind,
    RetentionPolicyRecord,
    RetentionScope,
    RetentionSweepOutcome,
)
from agent_runtime.persistence.records.subagents import (
    AsyncTaskRecord,
    SubagentResultRecord,
)
from agent_runtime.persistence.records.telemetry import (
    CapabilitySnapshotRecord,
    CompressionEventRecord,
    ModelPricingRecord,
    RuntimeModelCallUsageRecord,
    RuntimeRunUsageRecord,
    UsageDailyOrgRow,
    UsageDailyUserRow,
)
from agent_runtime.persistence.records.tool_budgets import (
    ToolBudgetEnforcement,
    ToolBudgetRecord,
)
from agent_runtime.persistence.records.tools import ToolInvocationRecord

PERSISTENCE_TABLE_RECORDS = (
    OutboxEventRecord,
    ConsumerCursorRecord,
    AsyncTaskRecord,
    SubagentResultRecord,
    ToolInvocationRecord,
    PersistenceApprovalRequestRecord,
    MemoryScopeRecord,
    MemoryItemRecord,
    ContextPayloadRecord,
    CompressionEventRecord,
    CapabilitySnapshotRecord,
    AuditLogRecord,
    CheckpointRecord,
)

__all__ = [
    "OutboxStatus",
    "AsyncTaskStatus",
    "ToolInvocationStatus",
    "ToolSideEffectClass",
    "ApprovalRiskClass",
    "PersistenceApprovalStatus",
    "RuntimeMemoryScopeType",
    "PayloadKind",
    "PayloadStorageBackend",
    "PayloadRedactionState",
    "AuditActorType",
    "AuditOutcome",
    "PersistenceValueNormalizer",
    "OutboxEventRecord",
    "RuntimeWorkerClaim",
    "RuntimeWorkerResult",
    "ConsumerCursorRecord",
    "AsyncTaskRecord",
    "SubagentResultRecord",
    "ToolInvocationRecord",
    "PersistenceApprovalRequestRecord",
    "MemoryScopeRecord",
    "MemoryItemRecord",
    "ContextPayloadRecord",
    "CompressionEventRecord",
    "CapabilitySnapshotRecord",
    "ModelPricingRecord",
    "RuntimeModelCallUsageRecord",
    "RuntimeRunUsageRecord",
    "UsageDailyOrgRow",
    "UsageDailyUserRow",
    "AuditLogRecord",
    "CheckpointRecord",
    "BudgetEnforcement",
    "BudgetPeriod",
    "BudgetRecord",
    "BudgetReservationRecord",
    "BudgetScope",
    "BudgetStateRecord",
    "BudgetStatus",
    "BudgetWithState",
    "ChargeOutcome",
    "ToolBudgetEnforcement",
    "ToolBudgetRecord",
    "RetentionKind",
    "RetentionPolicyRecord",
    "RetentionScope",
    "RetentionSweepOutcome",
    "PERSISTENCE_TABLE_RECORDS",
]

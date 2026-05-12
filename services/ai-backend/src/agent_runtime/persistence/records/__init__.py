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
from agent_runtime.persistence.records.citations import CitationRecord
from agent_runtime.persistence.records.drafts import (
    DraftPath,
    DraftRecord,
    DraftStatus,
)
from agent_runtime.persistence.records.common import (
    ApprovalRiskClass,
    AsyncTaskStatus,
    AuditActorType,
    AuditOutcome,
    OutboxStatus,
    PersistenceApprovalStatus,
    PersistenceValueNormalizer,
    ToolInvocationStatus,
    ToolSideEffectClass,
)
from agent_runtime.persistence.records.outbox import (
    ConsumerCursorRecord,
    OutboxEventRecord,
    RuntimeWorkerClaim,
    RuntimeWorkerResult,
)
from agent_runtime.persistence.records.retention import (
    RetentionKind,
    RetentionPolicyRecord,
    RetentionScope,
    RetentionSweepOutcome,
)
from agent_runtime.persistence.records.shares import (
    ShareRecipientRecord,
    ShareRecord,
    ShareViewAccess,
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
    UsageConversationAggregateRecord,
    UsageDailyConnectorRow,
    UsageDailyOrgRow,
    UsageDailyPurposeRow,
    UsageDailySubagentRow,
    UsageDailyUserRow,
)
from agent_runtime.persistence.records.tool_budgets import (
    ToolBudgetEnforcement,
    ToolBudgetRecord,
)
from agent_runtime.persistence.records.tool_ordinals import ToolOrdinalBindingRecord
from agent_runtime.persistence.records.tools import ToolInvocationRecord
from agent_runtime.persistence.records.workspace_feeds import (
    SourceAggregate,
    SubagentLifecycleStatus,
    SubagentSnapshot,
    SubagentTokenUsage,
)

PERSISTENCE_TABLE_RECORDS = (
    OutboxEventRecord,
    ConsumerCursorRecord,
    AsyncTaskRecord,
    SubagentResultRecord,
    ToolInvocationRecord,
    PersistenceApprovalRequestRecord,
    CompressionEventRecord,
    CapabilitySnapshotRecord,
    AuditLogRecord,
    CitationRecord,
    DraftRecord,
    ToolOrdinalBindingRecord,
)

__all__ = [
    "OutboxStatus",
    "AsyncTaskStatus",
    "ToolInvocationStatus",
    "ToolSideEffectClass",
    "ApprovalRiskClass",
    "PersistenceApprovalStatus",
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
    "CompressionEventRecord",
    "CapabilitySnapshotRecord",
    "ModelPricingRecord",
    "RuntimeModelCallUsageRecord",
    "RuntimeRunUsageRecord",
    "UsageConversationAggregateRecord",
    "UsageDailyConnectorRow",
    "UsageDailyOrgRow",
    "UsageDailyPurposeRow",
    "UsageDailySubagentRow",
    "UsageDailyUserRow",
    "AuditLogRecord",
    "CitationRecord",
    "DraftPath",
    "DraftRecord",
    "DraftStatus",
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
    "ToolOrdinalBindingRecord",
    "RetentionKind",
    "RetentionPolicyRecord",
    "RetentionScope",
    "RetentionSweepOutcome",
    "ShareRecipientRecord",
    "ShareRecord",
    "ShareViewAccess",
    "SourceAggregate",
    "SubagentLifecycleStatus",
    "SubagentSnapshot",
    "SubagentTokenUsage",
    "PERSISTENCE_TABLE_RECORDS",
]

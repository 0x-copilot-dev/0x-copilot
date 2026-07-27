"""Versioned, privacy-preserving harness-quality controls.

The package deliberately owns evaluation metadata and projections, not a
second copy of runtime events or user content.  Runtime events, effects,
citations, and usage remain authoritative in their existing stores.
"""

from agent_runtime.harness_quality.evaluation import (
    DeterministicEvaluationRunner,
    FixtureMiss,
    FixtureToolExecutor,
    InMemoryEvaluationRepository,
    PromotionGate,
    RuntimeTrajectoryProjector,
    TrajectoryProjector,
)
from agent_runtime.harness_quality.evaluation_contracts import (
    EvaluationCase,
    EvaluationMode,
    EvaluationResult,
    EvaluationStatus,
    FixtureResponse,
    HarnessVariant,
    PromotionAssessment,
    PromotionDecision,
    ProjectionPolicy,
    PromotionStatus,
    PromotionThresholds,
    ScorerResult,
    TrajectoryManifest,
)

__all__ = [
    "DeterministicEvaluationRunner",
    "EvaluationCase",
    "EvaluationMode",
    "EvaluationResult",
    "EvaluationStatus",
    "FixtureMiss",
    "FixtureResponse",
    "FixtureToolExecutor",
    "HarnessVariant",
    "InMemoryEvaluationRepository",
    "PromotionAssessment",
    "PromotionDecision",
    "ProjectionPolicy",
    "PromotionGate",
    "RuntimeTrajectoryProjector",
    "PromotionStatus",
    "PromotionThresholds",
    "ScorerResult",
    "TrajectoryManifest",
    "TrajectoryProjector",
]

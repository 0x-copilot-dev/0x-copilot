"""Typed, transport-neutral errors for the effect staging domain."""

from __future__ import annotations


class EffectStageError(Exception):
    """Base error with a stable, safe code for a future HTTP adapter."""

    code = "effect_stage_error"
    safe_message = "The proposed effect could not be staged."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.safe_message)
        if message is not None:
            self.safe_message = message


class EffectStageNotFound(EffectStageError):
    code = "effect_stage_not_found"
    safe_message = "No staged effect was found for this run."


class EffectStageForbidden(EffectStageError):
    code = "effect_stage_forbidden"
    safe_message = "You cannot change this staged effect."


class EffectStageStaleRevision(EffectStageError):
    code = "effect_stage_stale_revision"
    safe_message = "The proposal changed; review the latest revision before deciding."


class EffectStageInvalidTransition(EffectStageError):
    code = "effect_stage_invalid_transition"
    safe_message = "That decision is not valid for the current staged effect."


class EffectStageDigestMismatch(EffectStageError):
    code = "effect_stage_digest_mismatch"
    safe_message = "The displayed target or proposal changed; refresh and review again."


class EffectStageImmutableTarget(EffectStageError):
    code = "effect_stage_immutable_target"
    safe_message = "Changing the target requires a new staged effect."


class EffectStagePolicyBlocked(EffectStageError):
    code = "effect_stage_policy_blocked"
    safe_message = "This effect is blocked by the current policy."


class EffectStageNotStageable(EffectStageError):
    code = "effect_stage_not_stageable"
    safe_message = "This operation does not require an external-effect stage."


class EffectStageIdempotencyConflict(EffectStageError):
    code = "effect_stage_idempotency_conflict"
    safe_message = "This idempotency key was already used for a different request."


class EffectStageMalformedEvent(EffectStageError):
    code = "effect_stage_malformed_event"
    safe_message = "The staged-effect history is malformed."


class EffectStageProjectionUnbound(EffectStageError):
    code = "effect_stage_projection_unbound"
    safe_message = "The staged workspace change is not ready for approval yet."

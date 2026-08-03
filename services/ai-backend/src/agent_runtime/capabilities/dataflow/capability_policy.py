"""The concurrency vocabulary a dataflow ``batch_invoke`` node is judged by.

This used to live in the F6 batch-concurrency package, which is gone: LangGraph
schedules a turn's tool calls now, so the runtime has no batch planner, no permit
table, and no policy resolver that needed a shared vocabulary across four lanes.
The dataflow validator is the one surviving reader, and what it reads is four
fields, so the vocabulary moved to the module that consumes it rather than
outliving its own subsystem in a package nothing else imports.

The narrowing discipline is kept verbatim because it is the safety property, not
a stylistic one: members are declared **narrowest first**, so an instance built
with no arguments encodes no knowledge and therefore cannot authorize a batch.
``max_parallelism`` is the one deliberately non-safety field — ``None`` means the
capability declares no bound of its own, and the plan's own ceiling applies.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Final, Self

from pydantic import Field

from agent_runtime.execution.contracts import RuntimeContract


class ConcurrencyBounds:
    """The parallelism ceiling a declared capability policy is bound by."""

    SERIAL_PARALLELISM: Final[int] = 1
    MAX_PARALLELISM: Final[int] = 16


class NarrowableEnum(StrEnum):
    """Closed vocabulary ordered from most conservative to least conservative.

    Members must be declared narrowest first. A member added at the top of the
    declaration becomes the new conservative floor automatically; a member added
    at the bottom can never silently become the default.
    """

    @property
    def rank(self) -> int:
        """Return the authority rank, where ``0`` is the narrowest member."""

        return list(type(self)).index(self)

    @classmethod
    def conservative(cls) -> Self:
        """Return the narrowest member, used for unknown and absent metadata."""

        return next(iter(cls))

    @classmethod
    def narrowest(cls, *values: Self) -> Self:
        """Return the narrowest supplied member.

        The only composition operator for these vocabularies. It cannot return a
        value wider than any of its inputs.
        """

        if not values:
            raise ValueError("at least one value is required")
        return min(values, key=lambda value: value.rank)


class ConcurrencyMode(NarrowableEnum):
    """Concurrency posture declared by trusted capability metadata."""

    SERIAL = "serial"
    SAME_SUBJECT_SERIAL = "same_subject_serial"
    PARALLEL_SAFE = "parallel_safe"


class SideEffectKind(NarrowableEnum):
    """Side-effect class relevant to concurrent admission.

    ``UNKNOWN`` is narrower than an irreversible write: an undeclared effect
    class cannot be reasoned about at all, so it forbids every overlap.
    """

    UNKNOWN = "unknown"
    IRREVERSIBLE_WRITE = "irreversible_write"
    REVERSIBLE_WRITE = "reversible_write"
    READ = "read"
    NONE = "none"


class PolicySource(NarrowableEnum):
    """Trust source for concurrency metadata.

    Rank doubles as precedence. The most authoritative source
    (``PRODUCT_CATALOG``) establishes the policy; every less authoritative
    source is applied afterwards in descending rank order and may only narrow.
    """

    CONSERVATIVE_DEFAULT = "conservative_default"
    TRUSTED_PROVIDER = "trusted_provider"
    USER_APPROVED_OVERRIDE = "user_approved_override"
    PRODUCT_CATALOG = "product_catalog"


class ConcurrencyPolicy(RuntimeContract):
    """Declared concurrency posture for one dataflow-invocable capability.

    Every default is its vocabulary's conservative floor, so an instance built
    with no arguments cannot authorize parallel execution.
    """

    mode: ConcurrencyMode = ConcurrencyMode.conservative()
    side_effect: SideEffectKind = SideEffectKind.conservative()
    max_parallelism: int | None = Field(
        default=None,
        ge=ConcurrencyBounds.SERIAL_PARALLELISM,
        le=ConcurrencyBounds.MAX_PARALLELISM,
    )
    policy_source: PolicySource = PolicySource.conservative()


__all__ = (
    "ConcurrencyBounds",
    "ConcurrencyMode",
    "ConcurrencyPolicy",
    "NarrowableEnum",
    "PolicySource",
    "SideEffectKind",
)

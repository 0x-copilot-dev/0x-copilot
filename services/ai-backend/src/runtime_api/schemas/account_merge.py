"""Internal account-merge contracts (account-linking PRD §6.4).

The backend orchestrates the merge saga and calls
``POST /internal/v1/admin/account-merge`` over HTTP with explicit absorbed /
survivor coordinates. The request is deliberately not tenant-scoped — the
caller is the trusted backend service, authenticated by service token only.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, ValidationInfo, field_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.validation import ValueNormalizer


class AccountMergeRequest(RuntimeContract):
    """Re-key every tenant-scoped row of the absorbed account to the survivor.

    ``merge_id`` is the backend's ``account_merges`` saga id — echoed back so
    the orchestrator can correlate retries without trusting response ordering.
    """

    merge_id: str
    absorbed_org_id: str
    absorbed_user_id: str
    survivor_org_id: str
    survivor_user_id: str

    @field_validator(
        "merge_id",
        "absorbed_org_id",
        "absorbed_user_id",
        "survivor_org_id",
        "survivor_user_id",
        mode="before",
    )
    @classmethod
    def _normalize_ids(cls, value: object, info: ValidationInfo) -> str:
        return ValueNormalizer.normalize_id(value, info.field_name)


class AccountMergeResponse(RuntimeContract):
    """Per-store row-movement counts for one completed (or no-op) merge.

    ``status`` is ``"noop"`` when zero rows moved — the natural signal for an
    idempotent re-run after a completed merge. ``tables`` maps each touched
    table / store structure to the number of rows re-keyed; ``warnings``
    carries collision resolutions and skipped stores so the saga can log
    them against the ``account_merges`` row.
    """

    merge_id: str
    status: Literal["completed", "noop"]
    tables: dict[str, int] = Field(default_factory=dict)
    warnings: tuple[str, ...] = ()

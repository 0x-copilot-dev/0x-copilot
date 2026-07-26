"""Internal-only request contract for the E2 legacy migration prerequisite."""

from __future__ import annotations

from pydantic import Field, ValidationInfo, field_validator

from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.validation import ValueNormalizer


class LegacyMigrationRunRequest(RuntimeContract):
    """One trusted service-token call for one tenant and migration id.

    ``dry_run`` defaults to true so an operator has to opt into canonical
    artifact writes explicitly. The route is internal-only; the tenant id is
    an input to an authorized control-plane call, never a browser parameter.
    """

    org_id: str
    dry_run: bool = True
    batch_size: int = Field(default=25, ge=1, le=100)

    @field_validator("org_id", mode="before")
    @classmethod
    def _org_id(cls, value: object, info: ValidationInfo) -> str:
        return ValueNormalizer.normalize_id(value, info.field_name)

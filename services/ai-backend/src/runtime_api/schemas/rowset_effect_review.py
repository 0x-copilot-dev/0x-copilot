"""Strict requests for canonical row-set review commands."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

_SHA256 = r"^[a-f0-9]{64}$"


class RowSetDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: PositiveInt
    proposal_digest: str = Field(pattern=_SHA256)
    target_digest: str = Field(pattern=_SHA256)
    decisions: dict[str, Literal["approve", "hold"]] = Field(
        min_length=1,
        max_length=200,
    )

    @model_validator(mode="after")
    def validate_row_keys(self) -> "RowSetDecisionRequest":
        if any(not key or len(key) > 256 for key in self.decisions):
            raise ValueError("row decision keys are invalid")
        return self


class RowSetActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: PositiveInt
    proposal_digest: str = Field(pattern=_SHA256)
    target_digest: str = Field(pattern=_SHA256)
    row_keys: tuple[str, ...] = Field(min_length=1, max_length=200)
    basis_sequence_no: PositiveInt
    basis_ledger_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_row_keys(self) -> "RowSetActionRequest":
        if any(not key or len(key) > 256 for key in self.row_keys) or len(
            set(self.row_keys)
        ) != len(self.row_keys):
            raise ValueError("row action keys are invalid")
        return self


__all__ = ["RowSetActionRequest", "RowSetDecisionRequest"]

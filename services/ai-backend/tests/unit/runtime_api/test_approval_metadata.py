"""PR 4.4.6.2 — `McpApprovalMetadata` Pydantic validator.

Standalone test file because the metadata model has its own invariants
(param cap, vendor non-empty, enum-only category / reason / reversible)
that exist independently of the stream emitter that feeds it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from runtime_api.schemas.approvals import (
    APPROVAL_MAX_PARAMS,
    ApprovalParam,
    McpApprovalMetadata,
)
from runtime_api.schemas.common import (
    ApprovalCategory,
    ApprovalReasonCode,
    ApprovalReversible,
)


class TestMcpApprovalMetadata:
    def test_minimum_required_fields_construct(self) -> None:
        metadata = McpApprovalMetadata(
            vendor="LINEAR",
            category=ApprovalCategory.READ,
            reason_code=ApprovalReasonCode.READ_ONLY_FIRST_USE,
        )
        assert metadata.reversible is ApprovalReversible.NOT_APPLICABLE
        assert metadata.params == ()

    def test_params_capped_at_six(self) -> None:
        seven = tuple(
            ApprovalParam(label=f"L{i}", value=f"v{i}")
            for i in range(APPROVAL_MAX_PARAMS + 1)
        )
        with pytest.raises(ValidationError) as excinfo:
            McpApprovalMetadata(
                vendor="LINEAR",
                category=ApprovalCategory.READ,
                reason_code=ApprovalReasonCode.DEFAULT,
                params=seven,
            )
        assert "params capped at" in str(excinfo.value)

    def test_six_params_construct(self) -> None:
        six = tuple(
            ApprovalParam(label=f"L{i}", value=f"v{i}")
            for i in range(APPROVAL_MAX_PARAMS)
        )
        metadata = McpApprovalMetadata(
            vendor="LINEAR",
            category=ApprovalCategory.READ,
            reason_code=ApprovalReasonCode.DEFAULT,
            params=six,
        )
        assert len(metadata.params) == APPROVAL_MAX_PARAMS

    def test_vendor_must_be_non_empty(self) -> None:
        with pytest.raises(ValidationError):
            McpApprovalMetadata(
                vendor="",
                category=ApprovalCategory.READ,
                reason_code=ApprovalReasonCode.DEFAULT,
            )

    def test_vendor_capped_at_thirty_two(self) -> None:
        with pytest.raises(ValidationError):
            McpApprovalMetadata(
                vendor="X" * 33,
                category=ApprovalCategory.READ,
                reason_code=ApprovalReasonCode.DEFAULT,
            )

    def test_category_must_be_enum_member(self) -> None:
        with pytest.raises(ValidationError):
            McpApprovalMetadata(
                vendor="LINEAR",
                category="invalid",  # type: ignore[arg-type]
                reason_code=ApprovalReasonCode.DEFAULT,
            )

    def test_reason_code_must_be_enum_member(self) -> None:
        with pytest.raises(ValidationError):
            McpApprovalMetadata(
                vendor="LINEAR",
                category=ApprovalCategory.READ,
                reason_code="brand_new_reason",  # type: ignore[arg-type]
            )

    def test_extra_keys_round_trip(self) -> None:
        # `extra="allow"` is part of the contract so the metadata model
        # can sit inside a wider envelope (the existing JsonObject blob)
        # without losing pre-existing keys.
        metadata = McpApprovalMetadata.model_validate(
            {
                "vendor": "LINEAR",
                "category": "read",
                "reason_code": "default",
                "extra_field": "carry-through",
            }
        )
        dumped = metadata.model_dump(mode="json")
        assert dumped["extra_field"] == "carry-through"


class TestApprovalParam:
    def test_label_min_length(self) -> None:
        with pytest.raises(ValidationError):
            ApprovalParam(label="", value="value")

    def test_value_max_length(self) -> None:
        with pytest.raises(ValidationError):
            ApprovalParam(label="Channel", value="x" * 129)

    def test_hint_optional(self) -> None:
        param = ApprovalParam(label="Channel", value="#general")
        assert param.hint is None

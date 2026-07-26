"""Consent-card shape projection from real tool-call arguments."""

from __future__ import annotations

from runtime_api.schemas.approvals import (
    APPROVAL_MAX_ROWS,
    ApprovalPresentation,
    McpApprovalMetadata,
)
from runtime_api.schemas.common import ApprovalLayout, ApprovalRowStatus
from runtime_worker.approval_presentation import ApprovalPresentationProjector


class ArgumentsBuilderMixin:
    """Argument bags shaped like the calls each layout is meant to catch."""

    LONG_DRAFT = (
        "Launch Week is here. Over the next 7 days we're shipping what you "
        "asked for: local models, your keys, and agents that do real work."
    )

    @staticmethod
    def payout_batch() -> dict[str, object]:
        return {
            "safe": "0x8f42",
            "payouts": [
                {
                    "name": "mira.eth",
                    "role": "design",
                    "amount": "2,400 USDC",
                    "id": "p1",
                },
                {"name": "Jun Park", "role": "contracts", "amount": 3100, "id": "p2"},
            ],
        }

    @classmethod
    def slack_post(cls) -> dict[str, object]:
        return {"channel": "#launch-aurora", "text": cls.LONG_DRAFT}


class TestRowsLayout(ArgumentsBuilderMixin):
    def test_batch_argument_projects_decidable_rows(self) -> None:
        result = ApprovalPresentationProjector.project(
            tool_name="safe_create_payout_batch", arguments=self.payout_batch()
        )
        assert result is not None
        assert result.layout is ApprovalLayout.ROWS
        assert [row.label for row in result.rows] == ["mira.eth", "Jun Park"]
        assert [row.value for row in result.rows] == ["2,400 USDC", "3100"]
        assert all(row.decidable for row in result.rows)
        assert all(row.status is ApprovalRowStatus.PENDING for row in result.rows)

    def test_initials_cover_handles_and_human_names(self) -> None:
        result = ApprovalPresentationProjector.project(
            tool_name="safe_create_payout_batch", arguments=self.payout_batch()
        )
        assert result is not None
        assert [row.initials for row in result.rows] == ["MI", "JP"]

    def test_row_without_a_stable_id_is_display_only(self) -> None:
        # No id → nothing to key a per-row decision on. The row still renders
        # (the user must see the whole batch) but carries no buttons.
        result = ApprovalPresentationProjector.project(
            tool_name="pay_contributors",
            arguments={"payouts": [{"name": "mira.eth", "amount": "10 USDC"}]},
        )
        assert result is not None
        assert result.rows[0].decidable is False
        assert result.rows[0].row_id is None

    def test_entry_missing_label_or_amount_is_dropped(self) -> None:
        result = ApprovalPresentationProjector.project(
            tool_name="pay_contributors",
            arguments={
                "payouts": [
                    {"name": "mira.eth", "amount": "10 USDC", "id": "p1"},
                    {"note": "no label, no amount"},
                    {"name": "orphan"},
                ]
            },
        )
        assert result is not None
        assert [row.label for row in result.rows] == ["mira.eth"]

    def test_row_count_is_capped(self) -> None:
        entries = [
            {"name": f"payee-{i}", "amount": "1 USDC", "id": f"p{i}"} for i in range(40)
        ]
        result = ApprovalPresentationProjector.project(
            tool_name="pay_contributors", arguments={"payouts": entries}
        )
        assert result is not None
        assert len(result.rows) == APPROVAL_MAX_ROWS

    def test_all_entries_unusable_falls_through_to_preview_or_params(self) -> None:
        # A batch of junk must not render an empty ROWS card.
        result = ApprovalPresentationProjector.project(
            tool_name="pay_contributors", arguments={"payouts": [{"note": "x"}, 7, "s"]}
        )
        assert result is not None
        assert result.layout is ApprovalLayout.PARAMS

    def test_boolean_is_not_an_amount(self) -> None:
        result = ApprovalPresentationProjector.project(
            tool_name="pay_contributors",
            arguments={"payouts": [{"name": "mira.eth", "amount": True, "id": "p1"}]},
        )
        # ``True`` is not a value a user can consent to; the row is dropped.
        assert result is not None
        assert result.layout is ApprovalLayout.PARAMS


class TestPreviewLayout(ArgumentsBuilderMixin):
    def test_draft_text_is_the_preview_verbatim(self) -> None:
        result = ApprovalPresentationProjector.project(
            tool_name="slack_post_message", arguments=self.slack_post()
        )
        assert result is not None
        assert result.layout is ApprovalLayout.PREVIEW
        assert result.preview is not None
        # The card shows the exact string the connector receives — this is the
        # whole point of projecting from arguments rather than a description.
        assert result.preview.text == self.LONG_DRAFT

    def test_preview_meta_states_the_volume(self) -> None:
        result = ApprovalPresentationProjector.project(
            tool_name="slack_post_message", arguments=self.slack_post()
        )
        assert result is not None
        assert result.preview is not None
        assert result.preview.meta is not None
        assert "characters" in result.preview.meta

    def test_short_title_is_not_previewed(self) -> None:
        # A title already appears in the params frame; wrapping six words in a
        # preview box is noise, not consent.
        assert (
            ApprovalPresentationProjector.project(
                tool_name="linear_create_issue",
                arguments={"title": "Fix login", "team": "ENG"},
            )
            is None
        )

    def test_oversized_draft_is_truncated_to_the_contract_cap(self) -> None:
        result = ApprovalPresentationProjector.project(
            tool_name="slack_post_message", arguments={"text": "x" * 9000}
        )
        assert result is not None
        assert result.preview is not None
        assert len(result.preview.text) == 2000


class TestApproveVerb(ArgumentsBuilderMixin):
    def test_signing_tools_promise_a_signature(self) -> None:
        result = ApprovalPresentationProjector.project(
            tool_name="uniswap_execute_swap", arguments={"from": "ETH", "to": "USDC"}
        )
        assert result is not None
        assert result.approve_label == "Approve & sign"

    def test_sending_tools_promise_a_send(self) -> None:
        result = ApprovalPresentationProjector.project(
            tool_name="slack_post_message", arguments=self.slack_post()
        )
        assert result is not None
        assert result.approve_label == "Approve & send"

    def test_batch_verb_is_qualified_so_it_cannot_read_as_one_row(self) -> None:
        result = ApprovalPresentationProjector.project(
            tool_name="create_records",
            arguments={"records": [{"name": "a", "amount": "1", "id": "r1"}]},
        )
        assert result is not None
        assert result.approve_label == "Approve all"

    def test_neutral_tool_with_no_shape_projects_nothing(self) -> None:
        # Nothing to add over today's card — the caller keeps the params frame
        # and the wire payload stays byte-identical.
        assert (
            ApprovalPresentationProjector.project(
                tool_name="notion_get_page", arguments={"page_id": "abc"}
            )
            is None
        )


class TestPresentationContract:
    def test_declared_layout_without_content_falls_back(self) -> None:
        assert (
            ApprovalPresentation.model_validate({"layout": "rows"}).layout
            is ApprovalLayout.PARAMS
        )
        assert (
            ApprovalPresentation.model_validate({"layout": "preview"}).layout
            is ApprovalLayout.PARAMS
        )

    def test_unknown_keys_are_ignored_not_echoed(self) -> None:
        # The presentation block is narrative and reaches the client as-is;
        # it must not become a passthrough for arbitrary payload.
        dumped = ApprovalPresentation.model_validate(
            {"layout": "params", "script": "<img onerror=x>"}
        ).model_dump()
        assert "script" not in dumped

    def test_metadata_omits_presentation_when_absent(self) -> None:
        metadata = McpApprovalMetadata(
            vendor="NOTION", category="read", reason_code="read_only_first_use"
        )
        assert metadata.presentation is None

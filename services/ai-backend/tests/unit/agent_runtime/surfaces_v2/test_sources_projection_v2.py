"""Parity and safety pins for the pure Sources v2 provenance fold.

The canonical fixture is intentionally mirrored in
``packages/chat-surface/src/projections/sourcesV2.test.ts``.  It keeps the two
independent pure implementations aligned without adding a runtime dependency
between the Python service and the TypeScript package.
"""

from __future__ import annotations

from agent_runtime.surfaces_v2.ledger_models import LedgerEventType
from agent_runtime.surfaces_v2.sources import SourcesProjectionV2


RUN_ID = "run00000001abcdef"


def _event(
    event_type: str,
    sequence_no: int,
    payload: dict[str, object],
) -> dict[str, object]:
    return {
        "event_type": event_type,
        "sequence_no": sequence_no,
        "payload": payload,
    }


class TestSourcesProjectionV2:
    def test_fixture_and_source_id_prefix_match_typescript_projection(self) -> None:
        # Deliberately supplied out of order: both projectors sort by sequence.
        projection = SourcesProjectionV2.fold_raw(
            RUN_ID,
            [
                _event(
                    LedgerEventType.WRITE_APPLIED.value,
                    7,
                    {"connector_receipt_ref": "receipt://connector/receipt_7"},
                ),
                _event(
                    LedgerEventType.READ_EXECUTED.value,
                    1,
                    {
                        "connector": "linear",
                        "op": "get_issue",
                        "origin": "https://linear.app/team/ENG-142?token=not-output",
                    },
                ),
                _event(
                    LedgerEventType.ARTIFACT_CREATED.value,
                    2,
                    {
                        "artifact_id": "art_42",
                        "revision": 2,
                        "content_ref": "artifact://art_42/revisions/2",
                    },
                ),
                _event(
                    LedgerEventType.EFFECT_STAGED.value,
                    3,
                    {
                        "executor": "workspace",
                        "capability": "workspace",
                        "op": "replace",
                        "target_ref": "workspace-target://grant_finance/path_token_7",
                        "display_target": "Finance workspace change",
                    },
                ),
                _event(
                    LedgerEventType.EFFECT_STAGED.value,
                    4,
                    {
                        "executor": "browser",
                        "capability": "browser",
                        "op": "browser_submit",
                        "browser_origin": "https://portal.example.test/form?state=private",
                    },
                ),
                _event(
                    LedgerEventType.OPERATION_REQUESTED.value,
                    5,
                    {"capability": "sandbox", "op": "apply_patch"},
                ),
                _event(
                    "subagent.started",
                    6,
                    {"task_summary": "Compare the two implementation options."},
                ),
                _event(
                    LedgerEventType.EFFECT_APPLIED.value,
                    8,
                    {"receipt_ref": "receipt://effects/stage_8/claim_8"},
                ),
            ],
        )

        assert projection.v == 2
        assert projection.run_id == RUN_ID
        assert projection.latest_sequence_no == 8
        assert [(fact.sequence_no, fact.kind.value) for fact in projection.facts] == [
            (1, "connector"),
            (2, "artifact"),
            (3, "connector"),
            (3, "workspace"),
            (4, "connector"),
            (4, "browser"),
            (5, "connector"),
            (5, "sandbox"),
            (6, "subagent"),
            (7, "external_receipt"),
            (8, "external_receipt"),
        ]
        assert [fact.source_id for fact in projection.facts] == [
            "source:v2:001:connector",
            "source:v2:002:artifact",
            "source:v2:003:connector",
            "source:v2:003:workspace",
            "source:v2:004:connector",
            "source:v2:004:browser",
            "source:v2:005:connector",
            "source:v2:005:sandbox",
            "source:v2:006:subagent",
            "source:v2:007:external_receipt",
            "source:v2:008:external_receipt",
        ]
        assert all(fact.source_id.startswith("source:v2:") for fact in projection.facts)

        connector = projection.facts[0]
        assert connector.connector == "linear"
        assert connector.tool == "get_issue"
        assert connector.origin == "https://linear.app"

        artifact = projection.facts[1]
        assert artifact.artifact_id == "art_42"
        assert artifact.artifact_revision == 2
        assert artifact.artifact_source_ref == "artifact://art_42/revisions/2"

        workspace = projection.facts[3]
        assert workspace.workspace_grant_label == "Finance workspace change"
        assert (
            workspace.workspace_virtual_path_key
            == "workspace:v2:grant_finance:path_token_7"
        )
        assert projection.facts[5].browser_origin == "https://portal.example.test"
        assert projection.facts[7].sandbox_operation == "apply_patch"
        assert (
            projection.facts[8].subagent_task
            == "Compare the two implementation options."
        )
        assert (
            projection.facts[9].external_receipt_ref == "receipt://connector/receipt_7"
        )

    def test_hostile_values_do_not_leak_paths_secrets_arguments_or_bodies(self) -> None:
        projection = SourcesProjectionV2.fold_raw(
            RUN_ID,
            [
                _event(
                    LedgerEventType.READ_EXECUTED.value,
                    1,
                    {
                        "connector": "<img src=x onerror=alert(1)>",
                        "op": "</script>",
                        "arguments": {"api_key": "never-copy"},
                        "body": "never-copy-this-full-body",
                    },
                ),
                _event(
                    LedgerEventType.EFFECT_STAGED.value,
                    2,
                    {
                        "executor": "workspace",
                        "target_ref": "workspace-target://grant_01/path_token_01",
                        "display_target": "/srv/alice/private/project.txt",
                    },
                ),
                _event(
                    "browser.action",
                    3,
                    {
                        "browser_origin": "https://cookie@example.test/?token=secret-value",
                    },
                ),
                _event(
                    "sandbox.executed",
                    4,
                    {
                        "operation": "echo $OPENAI_API_KEY",
                        "command": "never-copy-command",
                    },
                ),
                _event(
                    "subagent.started",
                    5,
                    {"task": "cookie=session-secret"},
                ),
                _event(
                    LedgerEventType.WRITE_APPLIED.value,
                    6,
                    {"connector_receipt_ref": "receipt://provider?token=secret-value"},
                ),
                _event(
                    LedgerEventType.ARTIFACT_PROMOTED.value,
                    7,
                    {
                        "artifact_id": "art_safe",
                        "revision": 1,
                        "source_ref": "file:///Users/alice/private.txt",
                    },
                ),
                _event(
                    LedgerEventType.ARTIFACT_PROMOTED.value,
                    8,
                    {
                        "artifact_id": "art_provider_token",
                        "revision": 1,
                        "source_ref": "artifact://sk-proj-abcdefghijklmnop/revisions/1",
                    },
                ),
                _event(
                    "unknown.event",
                    9,
                    {"origin": "https://untrusted-origin.example.test/path"},
                ),
            ],
        )

        connector = projection.facts[0]
        # Hostile labels are still plain text, never treated as markup.
        assert connector.connector == "<img src=x onerror=alert(1)>"
        assert connector.tool == "</script>"

        workspace = next(
            fact for fact in projection.facts if fact.kind.value == "workspace"
        )
        assert workspace.workspace_grant_label is None
        assert (
            workspace.workspace_virtual_path_key
            == "workspace:v2:grant_01:path_token_01"
        )

        artifact = next(
            fact for fact in projection.facts if fact.kind.value == "artifact"
        )
        assert artifact.artifact_source_ref is None
        rendered = str(projection.model_dump(mode="json"))
        for forbidden in (
            "/srv/alice/private/project.txt",
            "OPENAI_API_KEY",
            "secret-value",
            "never-copy",
            "never-copy-this-full-body",
            "never-copy-command",
            "file:///Users/alice/private.txt",
            "sk-proj-abcdefghijklmnop",
            "untrusted-origin.example.test",
        ):
            assert forbidden not in rendered
        assert all(
            fact.kind.value
            not in {"browser", "sandbox", "subagent", "external_receipt"}
            for fact in projection.facts
        )

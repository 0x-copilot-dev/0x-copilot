from agent_runtime.artifacts.projection import ArtifactProjection
from agent_runtime.surfaces_v2.ledger_models import LedgerEventType


class TestArtifactProjection:
    def test_folds_created_revised_promoted_and_deduplicates_retry(self) -> None:
        created = {
            "event_type": LedgerEventType.ARTIFACT_CREATED.value,
            "payload": {
                "v": 1,
                "artifact_id": "art_018f47a6-7b2c-7a10-8f21-12345678a001",
                "kind": "document",
                "revision": 1,
                "content_ref": (
                    "artifact://art_018f47a6-7b2c-7a10-8f21-12345678a001/revisions/1"
                ),
                "content_digest": "a" * 64,
                "author": "model",
            },
        }
        revised = {
            "event_type": LedgerEventType.ARTIFACT_REVISED.value,
            "payload": {
                "v": 1,
                "artifact_id": "art_018f47a6-7b2c-7a10-8f21-12345678a001",
                "revision": 2,
                "parent_revision": 1,
                "content_ref": (
                    "artifact://art_018f47a6-7b2c-7a10-8f21-12345678a001/revisions/2"
                ),
                "content_digest": "b" * 64,
                "author": "user",
            },
        }
        promoted = {
            "event_type": LedgerEventType.ARTIFACT_PROMOTED.value,
            "payload": {
                "v": 1,
                "artifact_id": "art_018f47a6-7b2c-7a10-8f21-12345678a001",
                "source_ref": "message://msg_1",
                "kind": "document",
                "revision": 1,
            },
        }

        state = ArtifactProjection.fold((created, revised, revised, promoted))

        assert len(state.artifacts) == 1
        assert state.artifacts[0].current_revision == 2
        assert state.artifacts[0].source_ref == "message://msg_1"
        assert state.ignored_malformed_events == 0

    def test_malformed_or_out_of_order_events_fail_soft(self) -> None:
        state = ArtifactProjection.fold(
            (
                {
                    "event_type": LedgerEventType.ARTIFACT_REVISED.value,
                    "payload": {"v": 1},
                },
                {"event_type": "future.event", "payload": {"body": "ignored"}},
            )
        )

        assert state.artifacts == ()
        assert state.ignored_malformed_events == 1

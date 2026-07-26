"""File-native durability for the F-006 draft-to-effect safety correlation."""

from __future__ import annotations

import pytest

from agent_runtime.persistence.records import DraftEffectSupersession
from runtime_adapters.file._paths import FileStoreLayout
from runtime_adapters.file.draft_store import FileDraftStore

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _record(**changes: object) -> DraftEffectSupersession:
    values: dict[str, object] = {
        "org_id": "org_file_effect",
        "user_id": "user_file_effect",
        "draft_id": "deadbeefcafe1234deadbeefcafe1234",
        "stage_id": "stg_123e4567-e89b-42d3-a456-426614174000",
        "host_run_id": "run_later_host",
        "artifact_id": "art_f006_later_host",
        "proposal_digest": "a" * 64,
        "target_digest": "b" * 64,
    }
    values.update(changes)
    return DraftEffectSupersession.model_validate(values)


async def test_effect_supersession_survives_file_store_reopen_and_host_run_move(
    tmp_path,
) -> None:  # noqa: ANN001
    layout = FileStoreLayout(tmp_path / "draft-effect-store")
    first = FileDraftStore(layout)
    record = _record()

    persisted = await first.record_effect_supersession(record)
    replay = await first.record_effect_supersession(
        record.model_copy(update={"host_run_id": "run_later_host"})
    )
    reopened = FileDraftStore(layout)

    assert persisted == replay
    assert await reopened.has_effect_supersession(
        org_id=record.org_id,
        user_id=record.user_id,
        draft_id=record.draft_id,
    )
    assert not await reopened.has_effect_supersession(
        org_id=record.org_id,
        user_id="user_other",
        draft_id=record.draft_id,
    )


async def test_effect_supersession_rejects_conflicting_stage_reuse(tmp_path) -> None:  # noqa: ANN001
    store = FileDraftStore(FileStoreLayout(tmp_path / "draft-effect-store"))
    record = _record()
    await store.record_effect_supersession(record)

    with pytest.raises(ValueError, match="conflicts"):
        await store.record_effect_supersession(
            record.model_copy(update={"proposal_digest": "c" * 64})
        )

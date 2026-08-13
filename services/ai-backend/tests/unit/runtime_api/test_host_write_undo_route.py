"""The ``/host-writes`` routes, reached through the real composed app.

The capture side is tested in
``tests/unit/agent_runtime/capabilities/desktop/test_write_journal.py``. This
suite exists for the other half of the sentence: a journal nobody can read back
is not an undo, it is a log.

So nothing here injects a service. Every test builds the app the way the process
does — ``RuntimeAdapterFactory.from_settings`` over the FILE backend, then
``RuntimeApiAppFactory.create_app`` — writes into the journal through the store
the factory itself composed, and drives HTTP. If the composition root ever stops
assigning ``app.state.host_write_undo_service``, the route falls back to its 503
and these fail.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import pytest
from fastapi import status as http_status
from fastapi.testclient import TestClient

from copilot_service_contracts.deployment_profile import (
    ENV_DEPLOYMENT_PROFILE,
    PROFILE_SINGLE_USER_DESKTOP,
)
from copilot_service_contracts.headers import ORG_HEADER, USER_HEADER

from agent_runtime.api.conversation_coordinator import ConversationCoordinator
from agent_runtime.api.events import RuntimeEventProducer
from agent_runtime.api.host_write_undo_service import AUDIT_ACTION
from agent_runtime.api.run_coordinator import RunCoordinator
from agent_runtime.capabilities.desktop.write_journal import (
    HostWriteKind,
    HostWriteRecord,
    RevertStatus,
)
from agent_runtime.execution.models import ModelConfigResolver
from agent_runtime.settings import RuntimeSettings
from runtime_adapters.factory import RuntimeAdapterFactory
from runtime_adapters.file.runtime_api_store import FileRuntimeApiStore
from runtime_api.app import RuntimeApiAppFactory
from runtime_api.schemas import CreateConversationRequest, CreateRunRequest

ORG = "org_acme"
USER = "user_sarah"


class ComposedAppMixin:
    """Builds the real file-backed app and seeds a real run through it."""

    @pytest.fixture(autouse=True)
    def _desktop_profile(self, monkeypatch) -> None:
        """The file backend refuses to compose off the desktop profile.

        Read from ``os.environ`` inside the factory, not from settings, so it
        has to be set here rather than passed in.
        """

        monkeypatch.setenv(ENV_DEPLOYMENT_PROFILE, PROFILE_SINGLE_USER_DESKTOP)

    @staticmethod
    def settings(root: Path) -> RuntimeSettings:
        return RuntimeSettings.load(
            # `.env` is merged BEFORE `environ=`, so a developer's local file
            # would otherwise decide the store backend and this suite would be
            # green on a laptop and red on the first clean runner.
            env_file=os.devnull,
            environ={
                "OPENAI_API_KEY": "sk-test",
                "RUNTIME_DEFAULT_PROVIDER": "openai",
                "RUNTIME_DEFAULT_MODEL": "gpt-5.4-mini",
                "RUNTIME_STORE_BACKEND": "file",
                "RUNTIME_FILE_STORE_ROOT": str(root),
            },
        )

    @classmethod
    def compose(cls, root: Path):
        """The composition root, without opening the store.

        Kept separate so the two wiring assertions can inspect what
        composition produced without paying for a store lifecycle.
        """

        settings = cls.settings(root)
        ports = RuntimeAdapterFactory.from_settings(settings)
        return RuntimeApiAppFactory.create_app(ports=ports, settings=settings), ports

    @classmethod
    async def app(cls, root: Path):
        """Compose and open the file store, as the lifespan would."""

        app, ports = cls.compose(root)
        await ports.persistence.open()
        return app, ports

    @staticmethod
    async def seed_run(store: FileRuntimeApiStore, settings: RuntimeSettings) -> str:
        """Create a conversation + run on the real coordinators."""

        producer = RuntimeEventProducer(
            persistence=store, event_store=store, on_event_appended=None
        )
        runs = RunCoordinator(
            persistence=store,
            queue=store,
            event_producer=producer,
            settings=settings,
            model_resolver=ModelConfigResolver(settings),
        )
        conversations = ConversationCoordinator(
            persistence=store, settings=settings, run_coordinator=runs
        )
        conversation = await conversations.create_conversation(
            CreateConversationRequest(org_id=ORG, user_id=USER, assistant_id="a1")
        )
        run = await runs.create_run(
            CreateRunRequest(
                conversation_id=conversation.conversation_id,
                org_id=ORG,
                user_id=USER,
                user_input="Tidy my notes",
                model={"provider": "openai", "model_name": "gpt-5.4-mini"},
            )
        )
        return run.run_id

    @staticmethod
    def capture(ports, *, run_id: str, path: Path, prior: bytes, **kwargs) -> None:
        """Write one record into the SAME journal store the factory composed."""

        store = ports.host_write_journal_store
        store.append(
            HostWriteRecord(
                entry_id=kwargs.pop("entry_id", "entry-1"),
                org_id=kwargs.pop("org_id", ORG),
                conversation_id="conv-1",
                run_id=run_id,
                tool_call_id=kwargs.pop("tool_call_id", "call-1"),
                sequence=kwargs.pop("sequence", 1),
                path=str(path),
                authorized_root=kwargs.pop("authorized_root", str(path.parent)),
                kind=kwargs.pop("kind", HostWriteKind.MODIFIED),
                prior_sha256=store.put_blob(prior),
                prior_size=len(prior),
                captured_at=datetime.now(timezone.utc),
            )
        )

    @staticmethod
    def client(app, *, org_id: str = ORG, user_id: str = USER) -> TestClient:
        """A client carrying the verified-identity headers routes require.

        Identity comes from these headers only — the routes never read an
        ``org_id`` from the path, query or body — so this is also how the
        cross-tenant test asks the question as a different tenant.
        """

        return TestClient(app, headers={ORG_HEADER: org_id, USER_HEADER: user_id})

    @staticmethod
    def listing_path(run_id: str) -> str:
        return f"/v1/agent/runs/{run_id}/host-writes"

    @classmethod
    def revert_path(cls, run_id: str) -> str:
        return f"{cls.listing_path(run_id)}/revert"


class TestTheRouteIsReachable(ComposedAppMixin):
    """The seam the stalled agent left out: composition onto app state."""

    async def test_the_undo_service_is_composed_onto_app_state(self, tmp_path):
        """Without this assignment every call below answers 503 forever."""

        app, _ = self.compose(tmp_path / "store")

        assert getattr(app.state, "host_write_undo_service", None) is not None

    async def test_the_factory_composes_a_journal_store_on_the_file_backend(
        self, tmp_path
    ):
        """The API reads the same ledger the worker's capture side writes."""

        _, ports = self.compose(tmp_path / "store")

        assert ports.host_write_journal_store is not None

    async def test_listing_returns_what_was_captured(self, tmp_path):
        """A real GET, over the real app, returns the real record."""

        app, ports = await self.app(tmp_path / "store")
        run_id = await self.seed_run(
            ports.persistence, self.settings(tmp_path / "store")
        )
        target = tmp_path / "Projects" / "notes.md"
        target.parent.mkdir(parents=True)
        target.write_text("clobbered\n")
        self.capture(ports, run_id=run_id, path=target, prior=b"the original\n")

        response = self.client(app).get(self.listing_path(run_id))

        assert response.status_code == http_status.HTTP_200_OK
        body = response.json()
        assert body["run_id"] == run_id
        (entry,) = body["entries"]
        assert entry["path"] == str(target)
        assert entry["kind"] == HostWriteKind.MODIFIED.value
        assert entry["revertible"] is True
        assert entry["tool_call_id"] == "call-1"
        # The storage digest and the authorizing root are internal facts and
        # must not travel to a surface.
        assert "prior_sha256" not in entry
        assert "authorized_root" not in entry

    async def test_revert_over_http_puts_the_bytes_back(self, tmp_path):
        """The whole chain: route → service → reverter → the user's disk."""

        app, ports = await self.app(tmp_path / "store")
        run_id = await self.seed_run(
            ports.persistence, self.settings(tmp_path / "store")
        )
        target = tmp_path / "Projects" / "notes.md"
        target.parent.mkdir(parents=True)
        target.write_text("clobbered\n")
        self.capture(ports, run_id=run_id, path=target, prior=b"the original\n")

        response = self.client(app).post(self.revert_path(run_id), json={})

        assert response.status_code == http_status.HTTP_200_OK
        (outcome,) = response.json()["outcomes"]
        assert outcome["status"] == RevertStatus.RESTORED.value
        assert target.read_bytes() == b"the original\n"

    async def test_revert_narrowed_to_one_tool_call_leaves_the_rest(self, tmp_path):
        """``tool_call_id`` is what makes one bad edit cost only that edit."""

        app, ports = await self.app(tmp_path / "store")
        run_id = await self.seed_run(
            ports.persistence, self.settings(tmp_path / "store")
        )
        root = tmp_path / "Projects"
        root.mkdir(parents=True)
        bad, good = root / "bad.md", root / "good.md"
        bad.write_text("bad now\n")
        good.write_text("good now\n")
        self.capture(
            ports,
            run_id=run_id,
            path=bad,
            prior=b"bad before\n",
            entry_id="e-bad",
            tool_call_id="call-bad",
            sequence=1,
        )
        self.capture(
            ports,
            run_id=run_id,
            path=good,
            prior=b"good before\n",
            entry_id="e-good",
            tool_call_id="call-good",
            sequence=2,
        )

        response = self.client(app).post(
            self.revert_path(run_id), json={"tool_call_id": "call-bad"}
        )

        assert response.status_code == http_status.HTTP_200_OK
        (outcome,) = response.json()["outcomes"]
        assert outcome["path"] == str(bad)
        assert bad.read_bytes() == b"bad before\n"
        assert good.read_text() == "good now\n"


class TestRetentionIsSweptAtBoot(ComposedAppMixin):
    """The prune method is reachable, not a method nobody ever calls."""

    async def test_composing_the_app_drops_an_expired_capture(self, tmp_path):
        """Bounded retention has to happen somewhere. Boot is that somewhere."""

        root = tmp_path / "store"
        _, ports = self.compose(root)
        journal = ports.host_write_journal_store
        for entry_id, age_days in (("stale", 30), ("fresh", 1)):
            journal.append(
                HostWriteRecord(
                    entry_id=entry_id,
                    org_id=ORG,
                    conversation_id="conv-1",
                    run_id="run-1",
                    sequence=age_days,
                    path=f"/Users/ada/Projects/{entry_id}.md",
                    authorized_root="/Users/ada/Projects",
                    kind=HostWriteKind.MODIFIED,
                    prior_sha256=journal.put_blob(entry_id.encode()),
                    prior_size=len(entry_id),
                    captured_at=datetime.now(timezone.utc) - timedelta(days=age_days),
                )
            )

        # Boot again over the same root: composition is the sweep.
        _, rebooted = self.compose(root)

        surviving = rebooted.host_write_journal_store.records_for_run(
            org_id=ORG, run_id="run-1"
        )
        assert [record.entry_id for record in surviving] == ["fresh"]


class TestOwnershipAndAudit(ComposedAppMixin):
    """A revert writes to a disk, so the door in front of it has to hold."""

    async def test_an_unknown_run_is_404(self, tmp_path):
        app, _ = await self.app(tmp_path / "store")

        response = self.client(app).get(self.listing_path("run_does_not_exist"))

        assert response.status_code == http_status.HTTP_404_NOT_FOUND

    async def test_reverting_an_unknown_run_is_404(self, tmp_path):
        app, _ = await self.app(tmp_path / "store")

        response = self.client(app).post(
            self.revert_path("run_does_not_exist"), json={}
        )

        assert response.status_code == http_status.HTTP_404_NOT_FOUND

    async def test_a_record_for_another_org_is_not_listed(self, tmp_path):
        """Guessing a run id must not reach another tenant's history."""

        app, ports = await self.app(tmp_path / "store")
        run_id = await self.seed_run(
            ports.persistence, self.settings(tmp_path / "store")
        )
        target = tmp_path / "Projects" / "notes.md"
        target.parent.mkdir(parents=True)
        target.write_text("clobbered\n")
        self.capture(
            ports,
            run_id=run_id,
            path=target,
            prior=b"the original\n",
            org_id="org_someone_else",
        )

        body = self.client(app).get(self.listing_path(run_id)).json()

        assert body["entries"] == []

    async def test_a_revert_is_audited(self, tmp_path):
        """An unlogged undo is indistinguishable from the agent writing again."""

        app, ports = await self.app(tmp_path / "store")
        run_id = await self.seed_run(
            ports.persistence, self.settings(tmp_path / "store")
        )
        target = tmp_path / "Projects" / "notes.md"
        target.parent.mkdir(parents=True)
        target.write_text("clobbered\n")
        self.capture(ports, run_id=run_id, path=target, prior=b"the original\n")

        self.client(app).post(self.revert_path(run_id), json={})

        reverts = [
            record
            for event_type, record in ports.persistence.audit_log
            if event_type == AUDIT_ACTION
        ]
        assert len(reverts) == 1
        assert reverts[0]["resource_id"] == run_id
        # Per-path outcomes, not a bare tally: "which file came back" is the
        # only question worth asking after an undo.
        assert reverts[0]["metadata"]["outcomes"] == [
            {
                "path": str(target),
                "kind": HostWriteKind.MODIFIED.value,
                "status": RevertStatus.RESTORED.value,
            }
        ]

    async def test_a_revert_cannot_be_steered_at_a_path(self, tmp_path):
        """The body carries no path, digest or root — only a tool call id."""

        app, ports = await self.app(tmp_path / "store")
        run_id = await self.seed_run(
            ports.persistence, self.settings(tmp_path / "store")
        )
        victim = tmp_path / "elsewhere.txt"
        victim.write_text("untouched\n")

        response = self.client(app).post(
            self.revert_path(run_id),
            json={"path": str(victim), "prior_sha256": "0" * 64},
        )

        assert response.status_code == http_status.HTTP_200_OK
        assert response.json()["outcomes"] == []
        assert victim.read_text() == "untouched\n"

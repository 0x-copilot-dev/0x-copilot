"""Tests for ``/v1/library/search/stream`` — P7.5-A4.

Cross-audit §5.2 — assert the SSE envelope sequence: bm25_result →
vector_result → reranked → complete. Heartbeat cadence + Last-Event-ID
resume are tested at the framer level in test_search_rrf.py
(:func:`_sse_frame` is too thin to need a dedicated wire test).
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from backend_app.app import create_app
from backend_app.contracts import OrganizationRecord, UserRecord
from backend_app.identity.store import InMemoryIdentityStore
from backend_app.library.store import InMemoryLibraryStore
from backend_app.projects.store import InMemoryProjectsStore


def _client() -> TestClient:
    identity = InMemoryIdentityStore()
    identity.create_organization(
        OrganizationRecord(org_id="org_acme", display_name="Acme", slug="acme")
    )
    identity.create_user(
        UserRecord(
            user_id="usr_sarah",
            org_id="org_acme",
            primary_email="sarah@acme.com",
            display_name="Sarah",
        )
    )
    app = create_app(
        configure_logging_on_create=False,
        configure_telemetry_on_create=False,
        identity_store=identity,
        projects_store=InMemoryProjectsStore(),
        library_store=InMemoryLibraryStore(),
    )
    return TestClient(app)


def _q() -> dict[str, str]:
    return {"org_id": "org_acme", "user_id": "usr_sarah"}


def _seed_page(client: TestClient, title: str, markdown: str) -> str:
    resp = client.post(
        "/v1/library/pages",
        params=_q(),
        json={"title": title, "markdown": markdown},
    )
    assert resp.status_code == 201
    return resp.json()["id"]


def _parse_sse(raw: str) -> list[tuple[str, int, dict]]:
    """Parse the SSE stream into ``(event_name, sequence_no, payload)``
    tuples. Heartbeat comments (lines starting with ``:``) are skipped."""

    events: list[tuple[str, int, dict]] = []
    current_event: str | None = None
    current_id: int | None = None
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        if block.startswith(":"):  # heartbeat
            continue
        current_event = None
        current_id = None
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event: "):
                current_event = line[len("event: ") :].strip()
            elif line.startswith("id: "):
                current_id = int(line[len("id: ") :].strip())
            elif line.startswith("data: "):
                data_lines.append(line[len("data: ") :])
        if current_event is None or current_id is None or not data_lines:
            continue
        payload = json.loads("\n".join(data_lines))
        events.append((current_event, current_id, payload))
    return events


class TestSseHappyPath:
    def test_emits_full_envelope_sequence(self) -> None:
        client = _client()
        page_id = _seed_page(
            client, title="Rocket launch", markdown="approvals, demo, comms"
        )

        # Pull the full stream. TestClient supports streaming via the
        # GET method; the in-memory pipeline completes in a few ms so
        # the connection closes naturally after `library.search_complete`.
        with client.stream(
            "GET",
            "/v1/library/search/stream",
            params={**_q(), "q": "rocket"},
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            raw = b"".join(resp.iter_bytes()).decode("utf-8")

        events = _parse_sse(raw)
        names = [name for name, _, _ in events]
        # The four-event sequence in order. Reranked appears because
        # the route defaults to rerank=true for human callers (no
        # service-token header from TestClient).
        assert names == [
            "library.search_bm25_result",
            "library.search_vector_result",
            "library.search_reranked",
            "library.search_complete",
        ]

        # Sequence numbers are monotonic, starting at 1.
        seqs = [seq for _, seq, _ in events]
        assert seqs == [1, 2, 3, 4]

        # The final envelope carries the hydrated hit.
        final_payload = events[-1][2]
        assert final_payload["strategy"] == "bm25_only"
        assert any(hit["ref"]["id"] == page_id for hit in final_payload["hits"])
        assert isinstance(final_payload["took_ms"], int)
        assert final_payload["took_ms"] >= 0

    def test_correlation_id_is_stable_across_envelopes(self) -> None:
        client = _client()
        _seed_page(client, title="Alpha plan", markdown="content alpha")

        with client.stream(
            "GET",
            "/v1/library/search/stream",
            params={**_q(), "q": "alpha"},
        ) as resp:
            raw = b"".join(resp.iter_bytes()).decode("utf-8")

        events = _parse_sse(raw)
        correlation_ids = {payload["correlation_id"] for _, _, payload in events}
        assert len(correlation_ids) == 1, (
            f"All envelopes share one correlation_id; got {correlation_ids}"
        )

#!/usr/bin/env python3
"""G2A — real model-created CSV artifact and Studio dataset surface.

This is the artifact-only first half of G2. It deliberately requests no
workspace grant, opens no native folder picker, stages no filesystem effect,
and writes no local file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import DriverSession
from g2_csv_lifecycle import (
    CREATE_PROMPT,
    PreflightSkip,
    _artifact_detail,
    _assert_artifact_named_forecast,
    _assert_dataset_surface,
    _assert_initial_csv_semantics,
    _assert_no_plaintext_secret,
    _assert_no_workspace_apply,
    _assert_only_workspace_or_artifact_tools,
    _byok_provider,
    _dataset_artifact_from_run,
    _events,
    _journey_environment,
    _open_artifact_from_sources,
    _preflight_staged_runtime,
    _read_artifact_bytes,
    _wait_for_conversation_id,
    _wait_for_new_run,
    _wait_for_terminal_run,
)


def _result(outcome: str, reason: str | None = None) -> None:
    payload = {"journey": "G2A", "outcome": outcome}
    if reason is not None:
        payload["reason"] = reason
    print(json.dumps(payload, sort_keys=True), flush=True)


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        _result("skipped", str(exc))
        return 0

    _result("running", f"artifact-only; provider={provider}")
    with _journey_environment():
        session = DriverSession(
            name="generative-workflows-g2a-csv-artifact-surface",
        )
        completed = False
        try:
            with session:
                status = session.rpc("status")
                assert status.get("target") == "source"
                assert status.get("posture") == "prod"

                session.sign_in_local()
                session.ftue_add_key(provider, key)
                catalog = session.transport("GET", "/v1/agent/models")
                models = catalog.get("models", [])
                assert any(
                    isinstance(model, dict)
                    and model.get("provider") == provider
                    and model.get("configured") is True
                    for model in models
                ), "entered BYOK provider was not configured"

                session.send_first_run_message(CREATE_PROMPT)
                conversation_id = _wait_for_conversation_id(session)
                run_id = _wait_for_new_run(session, conversation_id, 0)
                _wait_for_terminal_run(session, run_id)
                events = _events(session, run_id)
                _assert_only_workspace_or_artifact_tools(events)
                _assert_no_workspace_apply(events)

                artifact = _dataset_artifact_from_run(events)
                _assert_artifact_named_forecast(
                    _artifact_detail(session, artifact.artifact_id)
                )
                _open_artifact_from_sources(session)
                _assert_dataset_surface(session)
                _assert_initial_csv_semantics(_read_artifact_bytes(session, artifact))
                session.shot("g2a-generated-csv-surface")
                completed = True
        finally:
            _assert_no_plaintext_secret(
                key,
                (session.run_dir, session._user_data_dir),
            )

    if completed:
        _result("passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

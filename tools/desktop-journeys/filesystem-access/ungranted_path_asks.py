#!/usr/bin/env python3
"""FS1 — an ungranted host-absolute path must never empty-succeed.

The live defect this verifies: the agent CLAIMED host paths and agent memory
ANSWERED them, so `ls ~/Downloads` returned an empty listing as a SUCCESS. The
whole apparatus (classifier, router, broker, consent card) was built and left
unwired, and 9151 unit tests were green over it. That is why this has to be a
live journey against the real supervised stack rather than another unit test.

Two things are asserted, and they are different claims:

  A. REACHABILITY — a directory the agent was never granted must NOT come back
     as a successful empty listing. A refusal or a consent request is a pass; a
     confident "that folder is empty" over a folder holding a known canary file
     is the defect.

  B. DEFAULT-ON — RUNTIME_ENABLE_DESKTOP_FILESYSTEM is deliberately NOT set
     here. The sibling G2 journeys set it to "1"; if this journey did too it
     would prove the flag works and say nothing about what a user gets out of
     the box, which is the thing that was actually asked for.

Privacy: the assertion runs against a journey-owned fixture directory, not the
user's real Downloads. A separate count-only probe touches Downloads to show
real host reach WITHOUT putting anyone's personal filenames into a screenshot
or a log. Same code path, no data exposure.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _lib import DriverSession  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "generative-workflows"))
from g2_csv_lifecycle import (  # noqa: E402
    PreflightSkip,
    _byok_provider,
    _events,
    _preflight_staged_runtime,
    _wait_for_conversation_id,
    _wait_for_new_run,
    _wait_for_terminal_run,
)

#: Everything G2 enables EXCEPT RUNTIME_ENABLE_DESKTOP_FILESYSTEM — see (B).
JOURNEY_ENVIRONMENT: Final = {
    "SURFACES_V2": "true",
    "ARTIFACT_EFFECTS_V2": "true",
    "ARTIFACT_DRAFTS_V2": "true",
    "OPERATION_GATEWAY_MODE": "enforce",
    "WORKSPACE_EFFECT_MODE": "enforce",
}
SECRET_ENVIRONMENT_NAMES: Final = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
)
FILESYSTEM_FLAG: Final = "RUNTIME_ENABLE_DESKTOP_FILESYSTEM"


@contextmanager
def _journey_environment() -> Iterator[None]:
    """Real workspace lane, no plaintext keys, and the FS flag left UNSET."""

    changed = (
        set(JOURNEY_ENVIRONMENT) | set(SECRET_ENVIRONMENT_NAMES) | {FILESYSTEM_FLAG}
    )
    previous = {name: os.environ.get(name) for name in changed}
    os.environ.update(JOURNEY_ENVIRONMENT)
    for name in SECRET_ENVIRONMENT_NAMES:
        os.environ.pop(name, None)
    # The point of the journey: unset, not "0" and not "1".
    os.environ.pop(FILESYSTEM_FLAG, None)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@contextmanager
def _fixture_directory() -> Iterator[tuple[Path, str, str]]:
    """A real on-disk directory the app was never granted."""

    nonce = uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix="fs1-ungranted-") as raw:
        root = Path(raw).resolve()
        canary_name = f"canary-{nonce}.txt"
        canary_body = f"fs1-canary-{nonce}"
        (root / canary_name).write_text(canary_body, encoding="utf-8")
        yield root, canary_name, canary_body


def _result(outcome: str, **extra: Any) -> None:
    print(
        json.dumps({"journey": "FS1", "outcome": outcome, **extra}, sort_keys=True),
        flush=True,
    )


def _downloads_entry_count() -> int | None:
    """How many entries Downloads really has — a number, never a name."""

    downloads = Path.home() / "Downloads"
    if not downloads.is_dir():
        return None
    try:
        return len(list(downloads.iterdir()))
    except OSError:
        return None


def _final_text(session: DriverSession, run_id: str) -> str:
    """The assistant's visible answer, lowercased for phrase matching."""

    chunks: list[str] = []
    for event in _events(session, run_id):
        if not isinstance(event, dict):
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        for key in ("text", "content", "message", "final_response", "delta"):
            value = payload.get(key)
            if isinstance(value, str):
                chunks.append(value)
    return "\n".join(chunks).lower()


def _tool_and_approval_shapes(events: list[dict]) -> dict[str, Any]:
    """Everything needed to tell a refusal from a silent empty success."""

    tool_events: list[dict] = []
    approvals: list[dict] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        name = str(event.get("event_type") or event.get("type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if "approval" in name:
            approvals.append(
                {
                    "event": name,
                    "kind": payload.get("approval_kind"),
                    "has_workspace_grant": "workspace_grant" in payload,
                }
            )
        if "tool" in name:
            tool_events.append(
                {
                    "event": name,
                    "tool": payload.get("tool_name") or payload.get("name"),
                    "status": payload.get("status"),
                    "summary": str(payload.get("summary") or "")[:400],
                }
            )
    return {"tools": tool_events, "approvals": approvals}


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        _result("skipped", reason=str(exc))
        return 0

    real_downloads = _downloads_entry_count()
    _result("running", provider=provider, downloads_entries=real_downloads)

    with _journey_environment(), _fixture_directory() as (root, canary, body):
        session = DriverSession(name="filesystem-access-fs1-ungranted")
        evidence: dict[str, Any] = {
            "fixture_root": str(root),
            "canary_name": canary,
            "flag_set_by_journey": FILESYSTEM_FLAG in os.environ,
            "downloads_entries_on_disk": real_downloads,
        }
        try:
            with session:
                status = session.rpc("status")
                evidence["target"] = status.get("target")
                evidence["posture"] = status.get("posture")

                session.sign_in_local()
                session.shot("fs1-01-signed-in")
                session.ftue_add_key(provider, key)
                session.shot("fs1-02-key-added")

                prompt = (
                    f"List the files in the directory {root}. "
                    "Report exactly what you find. If you cannot read it, say so "
                    "plainly and do not guess."
                )
                session.send_first_run_message(prompt)
                session.shot("fs1-03-prompt-sent")

                conversation_id = _wait_for_conversation_id(session)
                run_id = _wait_for_new_run(session, conversation_id, 0)
                evidence["run_id"] = run_id
                _wait_for_terminal_run(session, run_id)
                time.sleep(1.5)
                session.shot("fs1-04-run-terminal")

                events = _events(session, run_id)
                evidence["event_count"] = len(events)
                evidence.update(_tool_and_approval_shapes(events))
                answer = _final_text(session, run_id)
                evidence["mentions_canary"] = canary.lower() in answer
                evidence["claims_empty"] = any(
                    phrase in answer
                    for phrase in (
                        "is empty",
                        "no files",
                        "empty directory",
                        "directory is empty",
                        "folder is empty",
                        "contains no files",
                    )
                )
                evidence["answer_tail"] = answer[-1200:]
        finally:
            out = session.run_dir / "fs1-evidence.json"
            out.write_text(
                json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8"
            )
            print(f"[fs1] evidence -> {out}", flush=True)
            print(f"[fs1] shots    -> {session.run_dir}", flush=True)

    # The defect, stated as an assertion: a real directory holding a known file
    # must never come back as a successful empty listing.
    if evidence.get("claims_empty") and not evidence.get("mentions_canary"):
        _result(
            "FAILED",
            reason="ungranted path still empty-succeeds",
            **{k: evidence[k] for k in ("run_id", "claims_empty") if k in evidence},
        )
        return 1
    _result(
        "passed",
        mentions_canary=evidence.get("mentions_canary"),
        claims_empty=evidence.get("claims_empty"),
        approvals=len(evidence.get("approvals") or []),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

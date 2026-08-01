#!/usr/bin/env python3
"""FS-I — THE SPIKE: do staged workspace writes actually land bytes on disk?

One question, and the answer decides how much code the gen-UI-over-files work
needs:

    can a run read a CSV out of a folder the user attached, write it BACK to
    the same path, and have the real file on disk change?

Everything below the surface layer already exists and is executor-agnostic —
`WorkspaceEffectExecutor` (`EffectExecutorKind.WORKSPACE`),
`RuntimeWorkspaceProposalResolver`, `WorkspaceTargetRefCodec`, and
`EffectDispatchCoordinator`. What has never been observed here is whether that
chain is REACHABLE from an ordinary run and lands bytes.

    PASS => binding a gen-UI surface to its workspace source is the whole job;
            `StageCommitRequest` never has to learn about files.
    FAIL => the stage/commit contracts need a `target_workspace`, and the one
            hardcoded `executor=EffectExecutorKind.MCP`
            (staged_write_effect_dispatch.py) has to branch.

ENFORCE lane on purpose. `BrokeredWorkspaceBackend.supports_writes` is False
and reports no effect staging, so on the shipped DEFAULT lane the model is told
"`/workspace/` is strictly READ-ONLY" and there is no host write to observe at
all. That gap is its own decision (step 2); this spike is about whether the
machinery works when it is switched on.

Asserted on the FILESYSTEM — sha256 before vs after — never on the model's
narration. FS-D's tier 3 recorded `tools: []` beside "I can't write to that
path": the model declined before calling anything, so neither an approval nor a
denial was ever exercised and the run proved nothing. The tools actually called
are recorded here for exactly that reason: a run where no write was ATTEMPTED
must be distinguishable from one where a write was refused.
"""

from __future__ import annotations

import hashlib
import sys
import time
import uuid
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fs_journey_lib import (  # noqa: E402
    ENFORCE_LANE,
    DriverSession,
    PreflightSkip,
    _byok_provider,
    _preflight_staged_runtime,
    dump,
    events,
    lane,
    result,
    settle_run,
    tool_calls,
    wait_for_conversation_id,
    wait_for_new_run,
)

JOURNEY = "FS-I"

ATTACH = ".aui-folder-bar__attach"
CARD = "[data-testid^=tc-chat-approval-]"
APPROVE = "[data-testid^=tc-chat-approval-approve-]"

SEED_CSV = "region,q3\nnorth,120\nsouth,90\n"

#: Events that mean a host effect actually reached the filesystem.
APPLIED_EVENTS = ("write.applied", "effect.applied", "workspace.")


def _stub_dialogs(session: DriverSession, folder: Path) -> None:
    """Point the picker at ``folder`` and auto-accept the native approval.

    The picker stub is jG's. The `showMessageBox` stub is NEW and is the reason
    this can run unattended at all: the workspace commit authority raises a
    NATIVE approval, which Playwright cannot see and System Events will not
    drive without Accessibility (-25211).

    Stubbing a CONSENT dialog is a bigger claim than stubbing a file picker, so
    state it plainly: this spike proves the MECHANISM (do bytes land), not the
    consent UX. That half is already covered by FS-A, which clicks a real
    approval and reads the granted folder afterwards.
    """

    session.rpc(
        "mainEval",
        js="""({ dialog }, folder) => {
          dialog.showOpenDialog = async () => ({
            canceled: false,
            filePaths: [folder],
          });
          // Index 0 is the affirmative button on every showMessageBox this app
          // raises; `response` is what Electron returns for the click.
          dialog.showMessageBox = async () => ({ response: 0, checkboxChecked: false });
          return { stubbed: folder };
        }""",
        arg=str(folder),
    )


def _digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _approve_everything(session: DriverSession, seconds: int = 180) -> int:
    """Approve every card that appears; return how many were clicked.

    A single write can pause more than once (the ask, then the commit), so this
    does not assume one card.
    """

    clicked = 0
    deadline = time.time() + seconds
    while time.time() < deadline:
        if session.present(CARD) and session.present(APPROVE):
            try:
                session.rpc("clickLast", selector=APPROVE)
                clicked += 1
            except Exception:  # noqa: BLE001 — a card can resolve mid-click
                pass
        time.sleep(2.0)
    return clicked


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        result(JOURNEY, "skipped", reason=str(exc))
        return 3

    nonce = uuid.uuid4().hex[:8]
    folder = Path.home() / ".0xcopilot-journey-fixtures" / f"fs-i-{nonce}"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "seed.csv"
    target.write_text(SEED_CSV, encoding="utf-8")

    evidence: dict[str, Any] = {
        "granted_folder": str(folder),
        "target_file": str(target),
        "digest_before": _digest(target),
        "bytes_before": SEED_CSV,
    }

    with lane(ENFORCE_LANE):
        session = DriverSession(name="fs-i-workspace-writeback")
        try:
            with session:
                evidence["target_env"] = session.rpc("status").get("target")
                session.sign_in_local()
                session.ftue_add_key(provider, key)

                assert session.wait_for(ATTACH, timeout_s=60), "no folder bar"
                _stub_dialogs(session, folder)
                session.click(ATTACH)
                time.sleep(2.5)
                evidence["attached"] = session.evaluate(
                    "(() => { const n = document.querySelector("
                    "'.aui-folder-bar__name'); return n ? n.textContent : null; })()"
                )
                session.shot("i-01-folder-attached")

                # Deliberately phrased the way the ENFORCE guidance tells the
                # model to work: list the mounts, then act under
                # `/workspace/<mount>/`. The spike is about the write path, not
                # about whether the model can guess a route it was never given.
                session.send_first_run_message(
                    "Run `ls /workspace/` to find the mounted folder, read "
                    "seed.csv inside it, then write the file back to that same "
                    "path with one extra column named `note` whose value is "
                    "`checked` on every row. Use your filesystem tools."
                )
                conversation_id = wait_for_conversation_id(session)
                run_id = wait_for_new_run(session, conversation_id, 0)
                evidence["conversation_id"] = conversation_id
                evidence["run_id"] = run_id

                evidence["approvals_clicked"] = _approve_everything(session)
                settle_run(session, run_id, timeout_s=300)
                session.shot("i-02-after-run")

                stream = events(session, run_id)
                evidence["tools"] = tool_calls(stream)
                evidence["applied_events"] = sorted(
                    {
                        str(event.get("event_type") or event.get("type") or "")
                        for event in stream
                        if any(
                            marker
                            in str(event.get("event_type") or event.get("type") or "")
                            for marker in APPLIED_EVENTS
                        )
                    }
                )
        finally:
            evidence["digest_after"] = _digest(target)
            evidence["bytes_after"] = (
                target.read_text(encoding="utf-8") if target.is_file() else None
            )
            evidence["folder_contents"] = sorted(p.name for p in folder.iterdir())
            out = dump(session.run_dir, "fs-i-evidence.json", evidence)

    changed = evidence["digest_before"] != evidence["digest_after"]
    attempted = any(
        tool in {"write_file", "edit_file"} for tool in evidence.get("tools") or []
    )

    verdict = "passed" if changed else "FAILED"
    result(
        JOURNEY,
        verdict,
        file_changed=changed,
        write_attempted=attempted,
        tools=evidence.get("tools"),
        applied_events=evidence.get("applied_events"),
        approvals_clicked=evidence.get("approvals_clicked"),
        evidence=str(out),
    )
    return 0 if changed else 1


if __name__ == "__main__":
    raise SystemExit(main())

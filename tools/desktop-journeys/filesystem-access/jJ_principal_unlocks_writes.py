#!/usr/bin/env python3
"""FS-J — the adopted principal unlocks the enforced workspace lane.

Two boots of ONE install, which is the whole point: the thing being proven is
that boot 2 behaves differently from boot 1 because boot 1 left a principal
behind.

    boot 1  fresh install, no principal exists yet
            => no `E2_ROLLOUT_COHORTS_JSON`
            => `rollout_admission_denied+degraded_to_read_only`
            => reads work, writes do not

    boot 2  `resolveLocalPrincipal` ADOPTS the id boot 1's records are keyed by
            => the supervisor names it in a cohort rule
            => admission passes, and the lane composes for real

`fresh=False` pins `COPILOT_DESKTOP_USER_DATA_SUBDIR` to a stable directory so
both boots share one userData — the principal cache and the store both live
there, and pointing them at different directories is exactly the mistake that
would make this journey pass while proving nothing.

WHAT A FAILURE HERE MEANS. If boot 2 still degrades, read the reason: a
different tombstone (`no_host_session` / `host_session_has_no_base_read`) is the
NEXT gate, which has never executed in this program and is a genuine unknown —
not a regression in this change. `rollout_admission_denied` still appearing on
boot 2 means the emitted ids did not match the ones on the run records, which is
the one failure mode the whole adopt-never-mint design exists to avoid.
"""

from __future__ import annotations

import hashlib
import shutil
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

JOURNEY = "FS-J"
NAME = "fs-j-principal"

ATTACH = ".aui-folder-bar__attach"
CARD = "[data-testid^=tc-chat-approval-]"
APPROVE = "[data-testid^=tc-chat-approval-approve-]"

SEED_CSV = "region,q3\nnorth,120\nsouth,90\n"

WRITE_PROMPT = (
    "Run `ls /workspace/` to find the mounted folder, read seed.csv inside it, "
    "then write that file back to the same path with one extra column named "
    "`note` whose value is `checked` on every row. Use your filesystem tools."
)


def _user_data_dir() -> Path:
    """The dir BOTH boots share — `fresh=False` pins the `-reuse` suffix."""

    return (
        Path.home()
        / "Library"
        / "Application Support"
        / "0xCopilot"
        / f"journey-{NAME}-reuse"
    )


def _stub_dialogs(session: DriverSession, folder: Path) -> None:
    session.rpc(
        "mainEval",
        js="""({ dialog }, folder) => {
          dialog.showOpenDialog = async () => ({
            canceled: false, filePaths: [folder],
          });
          dialog.showMessageBox = async () => ({ response: 0, checkboxChecked: false });
          return { stubbed: folder };
        }""",
        arg=str(folder),
    )


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _approve_for(session: DriverSession, seconds: int) -> int:
    clicked = 0
    deadline = time.time() + seconds
    while time.time() < deadline:
        if session.present(CARD) and session.present(APPROVE):
            try:
                session.rpc("clickLast", selector=APPROVE)
                clicked += 1
            except Exception:  # noqa: BLE001 — a card may resolve mid-click
                pass
        time.sleep(2.0)
    return clicked


def _tombstone_reasons(user_data: Path) -> list[str]:
    """Every degrade reason the ai-backend recorded, newest last."""

    log = user_data / "logs" / "ai-backend.log"
    if not log.is_file():
        return []
    reasons: list[str] = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        marker = "workspace_effect.tombstone "
        if marker in line:
            reasons.append(line.split(marker, 1)[1].strip().rstrip('"}'))
    return reasons


def _boot(*, provider: str, key: str, folder: Path, send_write: bool) -> dict[str, Any]:
    """One full boot. Returns what that boot observed."""

    observed: dict[str, Any] = {}
    # INSTALLED PAYLOAD, not the source tree. C2 write authority requires a
    # signed-build attestation (`unsafe_dev_workspace_tcb` must be false and
    # the helper must pass Apple's designated-requirement check), which a
    # source launch cannot produce BY DESIGN. Proving writes therefore has to
    # happen against the artifact `copilot install` stages.
    session = DriverSession(name=NAME, fresh=False, installed_payload=True)
    with session:
        observed["target"] = session.rpc("status").get("target")
        # SETTLE FIRST, then branch. Probing `present()` the instant the driver
        # reports ready races the renderer: it answered False for a sign-in gate
        # that painted moments later, so the journey skipped sign-in and then
        # failed downstream looking for an FTUE card that was never going to
        # render. Three earlier failures were all this one bug wearing different
        # hats. Wait for SOME known screen before asking which one it is.
        assert session.wait_for(
            "[data-testid=sign-in-gate], .aui-folder-bar__attach", timeout_s=120
        ), "the app never reached a known screen"
        if session.present("[data-testid=sign-in-button]"):
            session.sign_in_local()
        else:
            observed["already_signed_in"] = True
        # Same rule: let the post-sign-in screen settle before deciding whether
        # a key still has to be added. The macOS keychain is MACHINE-scoped, so
        # a wiped userData does not imply a keyless install — "fresh install"
        # and "fresh profile" are different things.
        session.wait_for(
            "[data-testid=first-run-add-key], .aui-folder-bar__attach", timeout_s=120
        )
        # The key survives in the keychain, so boot 2 has no FTUE gate to fill.
        # A blanket try/except here is what made boot 1 fail SILENTLY at the
        # folder bar: it swallowed a real add-key failure and left the gate up.
        # So a failure is only tolerated when the composer is demonstrably
        # already past it.
        if not session.present(ATTACH):
            try:
                session.ftue_add_key(provider, key)
            except Exception:  # noqa: BLE001 — re-raised below unless benign
                if not session.wait_for(ATTACH, timeout_s=20):
                    raise
                observed["key_already_present"] = True
        assert session.wait_for(ATTACH, timeout_s=60), "no folder bar"
        _stub_dialogs(session, folder)
        session.click(ATTACH)
        time.sleep(2.5)
        observed["attached"] = session.evaluate(
            "(() => { const n = document.querySelector('.aui-folder-bar__name');"
            " return n ? n.textContent : null; })()"
        )

        session.send_first_run_message(
            WRITE_PROMPT if send_write else "Say READY and nothing else."
        )
        conversation_id = wait_for_conversation_id(session)
        run_id = wait_for_new_run(session, conversation_id, 0)
        observed["approvals_clicked"] = _approve_for(session, 90 if send_write else 20)
        settle_run(session, run_id, timeout_s=300)
        observed["tools"] = tool_calls(events(session, run_id))
    return observed


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        result(JOURNEY, "skipped", reason=str(exc))
        return 3

    nonce = uuid.uuid4().hex[:8]
    folder = Path.home() / ".0xcopilot-journey-fixtures" / f"fs-j-{nonce}"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / "seed.csv"
    target.write_text(SEED_CSV, encoding="utf-8")

    user_data = _user_data_dir()
    shutil.rmtree(user_data, ignore_errors=True)  # a genuinely fresh install

    evidence: dict[str, Any] = {
        "user_data_dir": str(user_data),
        "granted_folder": str(folder),
        "digest_before": _digest(target),
    }

    with lane(ENFORCE_LANE):
        # Boot 1 — no principal exists; this boot is what creates one.
        evidence["boot1"] = _boot(
            provider=provider, key=key, folder=folder, send_write=False
        )
        evidence["boot1_reasons"] = _tombstone_reasons(user_data)
        evidence["principal_after_boot1"] = (
            (user_data / "local-principal.json").read_text(encoding="utf-8")
            if (user_data / "local-principal.json").is_file()
            else None
        )

        # Boot 2 — the principal is now adoptable, so the cohort names it.
        evidence["boot2"] = _boot(
            provider=provider, key=key, folder=folder, send_write=True
        )
        evidence["boot2_reasons"] = _tombstone_reasons(user_data)
        # Written by `resolveLocalPrincipal` the moment it ADOPTS — so it is
        # absent after boot 1 (nothing to adopt yet) and present after boot 2.
        # That asymmetry is the mechanism, stated as evidence.
        evidence["principal_after_boot2"] = (
            (user_data / "local-principal.json").read_text(encoding="utf-8")
            if (user_data / "local-principal.json").is_file()
            else None
        )

    evidence["digest_after"] = _digest(target)
    evidence["bytes_after"] = (
        target.read_text(encoding="utf-8") if target.is_file() else None
    )
    out = dump(
        Path("tools/desktop-journeys/runs") / NAME, "fs-j-evidence.json", evidence
    )

    boot2_reasons = evidence["boot2_reasons"]
    newly = boot2_reasons[len(evidence["boot1_reasons"]) :]
    admitted = not any("rollout_admission_denied" in reason for reason in newly)
    changed = evidence["digest_before"] != evidence["digest_after"]

    result(
        JOURNEY,
        "passed" if (admitted and changed) else "FAILED",
        cohort_admitted_on_boot2=admitted,
        file_changed=changed,
        principal_adopted=evidence.get("principal_after_boot2") is not None,
        boot2_reasons=newly,
        boot2_tools=evidence["boot2"].get("tools"),
        evidence=str(out),
    )
    return 0 if (admitted and changed) else 1


if __name__ == "__main__":
    raise SystemExit(main())

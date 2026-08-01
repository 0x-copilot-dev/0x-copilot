#!/usr/bin/env python3
"""FS-C + FS-F — the composer's control row, and the agent's own scratch on disk.

Two claims that happen to share one boot, kept as two verdicts.

FS-C (the composer)
    * the `+` menu must NOT offer "Attach Folder" — on the first-run composer or
      the run composer. PRD-FS-10 deleted that row: a folder grant copies
      nothing into the message and OUTLIVES it, so housing it beside "Attach
      Image" taught the wrong model, and two entry points to one capability is
      how the grant model got muddled.
    * the folder bar sits ON the composer frame before the first message and is
      GONE after it (the bar is scoped to the moment; the access is not).
    * the bottom row reads `+ · Tools · execution-mode · <spacer> · model · mic ·
      send`, measured by real on-screen geometry rather than DOM order, because
      order is what a user sees and CSS can reorder DOM.

FS-F (the scratch)
    * after a real run, `<COPILOT_HOME>/.tmp/<conversation_id>/` exists, carries
      `meta.json`, and the run's tool-result / subagent tiers are real
      inspectable files.

      The journey probes BOTH the COPILOT_HOME this harness launched with and
      the `~/.0xcopilot` default, and reports which one the running service
      actually chose. That is deliberate: `agent_scratch_root()` reads
      `COPILOT_HOME` from the SERVICE's environment, and the desktop supervisor
      hands its children a curated allowlist — so where the scratch lands is a
      question about the shipped env plumbing, not something to assume.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _fs_journey_lib import (  # noqa: E402
    DEFAULT_LANE,
    DriverSession,
    PreflightSkip,
    _byok_provider,
    _preflight_staged_runtime,
    dump,
    lane,
    result,
    settle_run,
    tool_calls,
    events,
    transport_json,
    wait_for_conversation_id,
    wait_for_new_run,
)

PLUS = 'button[aria-label="Open attachment and tools menu"]'
FOLDER_BAR = ".aui-folder-bar"
FOLDER_BAR_ATTACH = ".aui-folder-bar__attach"

#: The bottom row, named by what a user reaches for.
CONTROLS = {
    "plus": PLUS,
    "tools": "[data-testid=first-run-tools-button]",
    "bypass": ".atlas-bypass-pill",
    "model": ".atlas-model-pill",
    "mic": ".atlas-composer-mic",
    "send": 'button[aria-label="Send message"]',
}

_MENU_SELECTOR = '.ui-pop__row, [role="menuitem"], button'


def _rects(session: DriverSession) -> dict[str, Any]:
    """Where each control actually is on screen — left edge + size."""

    js = (
        "(() => { const sel = " + json.dumps(CONTROLS) + ";"
        " const out = {};"
        " for (const [name, q] of Object.entries(sel)) {"
        "   const el = document.querySelector(q);"
        "   if (!el) { out[name] = null; continue; }"
        "   const r = el.getBoundingClientRect();"
        "   out[name] = { left: Math.round(r.left), width: Math.round(r.width),"
        "     visible: r.width > 0 && r.height > 0,"
        "     text: (el.innerText || '').trim().slice(0, 40),"
        "     disabled: el.hasAttribute('disabled') };"
        " } return out; })()"
    )
    return session.evaluate(js) or {}


def _menu_rows(session: DriverSession) -> list[str]:
    js = (
        "(() => Array.from(document.querySelectorAll('"
        + _MENU_SELECTOR
        + "')).map((el) => (el.textContent || '').trim()).filter(Boolean)"
        ".map((t) => t.slice(0, 60)))()"
    )
    return session.evaluate(js) or []


def _attach_folder_row(session: DriverSession) -> Any:
    js = (
        "(() => { const hit = Array.from(document.querySelectorAll('"
        + _MENU_SELECTOR
        + "')).find((el) => /attach folder/i.test(el.textContent || ''));"
        " if (!hit) return null; const r = hit.getBoundingClientRect();"
        " return { text: (hit.textContent || '').trim().slice(0, 120),"
        " visible: r.width > 0 && r.height > 0 }; })()"
    )
    return session.evaluate(js)


def _folder_bar(session: DriverSession) -> Any:
    js = (
        "(() => { const bar = document.querySelector('"
        + FOLDER_BAR
        + "'); if (!bar) return null; const r = bar.getBoundingClientRect();"
        " const attach = document.querySelector('" + FOLDER_BAR_ATTACH + "');"
        " return { text: (bar.innerText || '').trim().slice(0, 160),"
        " attach_text: attach ? (attach.innerText || '').trim().slice(0, 120) : null,"
        " visible: r.width > 0 && r.height > 0, top: Math.round(r.top) }; })()"
    )
    return session.evaluate(js)


def _order(rects: dict[str, Any]) -> list[str]:
    present = [
        (name, value["left"])
        for name, value in rects.items()
        if isinstance(value, dict) and value.get("visible")
    ]
    return [name for name, _ in sorted(present, key=lambda pair: pair[1])]


def _scratch_probe(copilot_home: Path, conversation_id: str) -> dict[str, Any]:
    root = copilot_home / ".tmp"
    conversation = root / conversation_id
    probe: dict[str, Any] = {
        "scratch_root": str(root),
        "root_exists": root.is_dir(),
        "conversation_dir": str(conversation),
        "conversation_exists": conversation.is_dir(),
    }
    if root.is_dir():
        probe["root_children"] = sorted(p.name for p in root.iterdir())[:20]
    if not conversation.is_dir():
        return probe
    listing: list[str] = []
    for path in sorted(conversation.rglob("*")):
        suffix = "/" if path.is_dir() else f" ({path.stat().st_size}B)"
        listing.append(str(path.relative_to(conversation)) + suffix)
    probe["tree"] = listing[:200]
    # The claim is that tool results and subagent transcripts are INSPECTABLE
    # FILES, so count files rather than directories: an empty `tool-results/`
    # proves the provisioner ran and nothing else.
    probe["files"] = [
        str(path.relative_to(conversation))
        for path in sorted(conversation.rglob("*"))
        if path.is_file()
    ][:100]
    probe["tool_result_files"] = [
        name for name in probe["files"] if "/tool-results/" in f"/{name}"
    ]
    probe["subagent_files"] = [
        name for name in probe["files"] if "/subagents/" in f"/{name}"
    ]
    meta = conversation / "meta.json"
    probe["meta_exists"] = meta.is_file()
    if meta.is_file():
        try:
            probe["meta"] = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            probe["meta_error"] = repr(exc)
    return probe


def main() -> int:
    try:
        _preflight_staged_runtime()
        provider, key = _byok_provider()
    except PreflightSkip as exc:
        result("FS-C", "skipped", reason=str(exc))
        result("FS-F", "skipped", reason=str(exc))
        return 3

    import os

    launched_home = Path(os.environ.get("COPILOT_HOME") or (Path.home() / ".0xcopilot"))
    evidence: dict[str, Any] = {"launched_copilot_home": str(launched_home)}

    with lane(DEFAULT_LANE):
        session = DriverSession(name="fs-c-composer-and-scratch")
        try:
            with session:
                evidence["target"] = session.rpc("status").get("target")
                session.sign_in_local()
                session.ftue_add_key(provider, key)
                assert session.wait_for(PLUS, timeout_s=60), "composer never appeared"
                session.shot("c-01-first-run-composer")

                # --- FS-C, before the first message --------------------------
                evidence["ftue_folder_bar"] = _folder_bar(session)
                evidence["ftue_controls"] = _rects(session)
                evidence["ftue_order"] = _order(evidence["ftue_controls"])
                session.click(PLUS)
                time.sleep(0.4)
                session.shot("c-02-first-run-plus-menu")
                evidence["ftue_menu_rows"] = _menu_rows(session)
                evidence["ftue_attach_folder_row"] = _attach_folder_row(session)
                session.press("body", "Escape")
                time.sleep(0.3)

                # --- one real run, so FS-F has something to inspect ----------
                # A subagent, because the scratch's `subagents/` tier is one of
                # the two things FS-F claims are inspectable files.
                session.send_first_run_message(
                    "Use exactly one subagent to write a four-line poem about "
                    "the sea. Reply with just the poem."
                )
                conversation_id = wait_for_conversation_id(session)
                run_id = wait_for_new_run(session, conversation_id, 0)
                evidence["conversation_id"] = conversation_id
                evidence["run_id"] = run_id
                assert session.wait_for(PLUS, timeout_s=90), (
                    "run composer never appeared"
                )
                # The folder bar is a BEFORE/AFTER claim about the first message,
                # so read it while the run is live — it must already be gone.
                evidence["run_folder_bar_live"] = _folder_bar(session)

                final = settle_run(session, run_id, timeout_s=300)
                evidence["run_status"] = final.get("status")
                stream = events(session, run_id)
                evidence["run_tools"] = tool_calls(stream)

                # --- FS-C, after the first message ---------------------------
                # Measured once the run has SETTLED: while a run is in flight the
                # trailing button is Stop, not Send, so an in-flight reading
                # would report a missing control that is merely doing its job.
                time.sleep(3)
                evidence["run_folder_bar"] = _folder_bar(session)
                evidence["run_controls"] = _rects(session)
                evidence["run_order"] = _order(evidence["run_controls"])
                session.shot("c-03-run-composer")
                session.click(PLUS)
                time.sleep(0.4)
                session.shot("c-04-run-plus-menu")
                evidence["run_menu_rows"] = _menu_rows(session)
                evidence["run_attach_folder_row"] = _attach_folder_row(session)
                session.press("body", "Escape")
                session.shot("c-05-run-done")

                # --- FS-F ----------------------------------------------------
                evidence["scratch_at_launched_home"] = _scratch_probe(
                    launched_home, conversation_id
                )
                evidence["scratch_at_default_home"] = _scratch_probe(
                    Path.home() / ".0xcopilot", conversation_id
                )
                # What the app itself says about the conversation, so the
                # meta.json title claim can be checked against the real title.
                try:
                    evidence["conversation"] = transport_json(
                        session, "GET", f"/v1/agent/conversations/{conversation_id}"
                    )
                except Exception as exc:  # noqa: BLE001
                    evidence["conversation_error"] = repr(exc)[:200]
        finally:
            out = dump(session.run_dir, "fs-c-f-evidence.json", evidence)
            print(f"[fs-c/f] evidence -> {out}", flush=True)

    # ---- FS-C verdict ------------------------------------------------------
    c_failures: list[str] = []
    if evidence.get("ftue_attach_folder_row") or evidence.get("run_attach_folder_row"):
        c_failures.append("the `+` menu still offers Attach Folder")
    ftue_bar = evidence.get("ftue_folder_bar")
    if not ftue_bar or not ftue_bar.get("visible"):
        c_failures.append("no folder bar on the composer before the first message")
    if evidence.get("run_folder_bar") or evidence.get("run_folder_bar_live"):
        c_failures.append("the folder bar is still showing after the first message")
    order = evidence.get("run_order") or []
    expected = ["plus", "tools", "bypass", "model", "mic", "send"]
    present_expected = [name for name in expected if name in order]
    if [name for name in order if name in expected] != present_expected:
        c_failures.append(f"control row order is {order}, expected {expected}")
    missing = [name for name in expected if name not in order]
    if missing:
        c_failures.append(f"control row is missing {missing}")

    if c_failures:
        result("FS-C", "FAILED", reasons=c_failures, run_order=order)
    else:
        result("FS-C", "passed", run_order=order, ftue_bar=ftue_bar.get("attach_text"))

    # ---- FS-F verdict ------------------------------------------------------
    launched = evidence.get("scratch_at_launched_home", {})
    default = evidence.get("scratch_at_default_home", {})
    chosen = (
        launched
        if launched.get("conversation_exists")
        else default
        if default.get("conversation_exists")
        else None
    )
    f_failures: list[str] = []
    if chosen is None:
        f_failures.append(
            "no `<COPILOT_HOME>/.tmp/<conversation_id>/` was created at either "
            f"{launched.get('scratch_root')!r} or {default.get('scratch_root')!r}"
        )
    else:
        if not chosen.get("meta_exists"):
            f_failures.append("the conversation scratch has no meta.json")
        if not chosen.get("tree"):
            f_failures.append("the conversation scratch is empty")
        if not chosen.get("tool_result_files") and not chosen.get("subagent_files"):
            f_failures.append(
                "the run's tool-results/ and subagents/ tiers exist but hold no "
                "files — nothing about this run is inspectable there"
            )

    if f_failures:
        result(
            "FS-F",
            "FAILED",
            reasons=f_failures,
            launched_home_root=launched.get("scratch_root"),
            default_home_root=default.get("scratch_root"),
        )
        return 1
    result(
        "FS-F",
        "passed",
        scratch_root=chosen.get("scratch_root"),
        entries=len(chosen.get("tree") or []),
        tool_result_files=len(chosen.get("tool_result_files") or []),
        subagent_files=len(chosen.get("subagent_files") or []),
        meta_title=(chosen.get("meta") or {}).get("title"),
    )
    return 0 if not c_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())

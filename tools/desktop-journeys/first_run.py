#!/usr/bin/env python3
"""first-run — sign-in, BYOK, model preselect, and where the first message lands.

One boot, keyed with ONE provider, walked in the order a new user meets it. The
phases are ordered because the state is cumulative: FR-2 spends the virgin FTUE,
FR-5 binds a run, and FR-6 needs a bound run to prove New-chat leaves it. They
cannot be reordered, only removed.

The whole group is deliberately keyed with **Anthropic only**. That is not an
arbitrary provider choice — it is the fixture. An Anthropic-only profile is what
makes FR-3/FR-4 falsifiable: the catalog still carries OpenAI models, they must
read `configured=false`, and the composer must refuse to preselect one. Keying a
second provider would quietly delete that assertion. Pass another provider to
exercise the BYOK half elsewhere; the preselect phases then skip rather than
pretend.

    python3 tools/desktop-journeys/first_run.py
    python3 tools/desktop-journeys/first_run.py openai   # FR-3/4/5 skip

Folds in: provider-key-byok/byok_first_run, chat-nav-model/{model_preselect,
ftue_first_message, new_chat}.

The key is read ONLY from services/ai-backend/.env via load_env_key and is never
printed, logged, or committed — only lengths and status codes ever surface.
"""

from __future__ import annotations

import os
import sys
import time

from _lib import (
    DriverSession,
    JourneyPlan,
    load_env_key,
    load_env_value,
    require,
    runs_for_conversation,
    wait_for_conversation_id,
)

# argv provider → (catalog provider slug, substring the model pill should show).
# The facade normalizes some slugs on the catalog (e.g. google → gemini).
PROVIDER_SPEC = {
    "openai": ("openai", "gpt"),
    "anthropic": ("anthropic", "claude"),
    "openrouter": ("openrouter", ""),
}

# The deployment default the catalog always leads with. DERIVED, not hardcoded:
# the catalog serves `RUNTIME_DEFAULT_MODEL` verbatim, so reading the same
# setting keeps this assertion true across vendor releases. The literal fallback
# mirrors the runtime's own compiled-in default (agent_runtime/settings.py) and
# is the value used when no .env is present — e.g. in a branch worktree.
DEFAULT_MODEL_ID = load_env_value("RUNTIME_DEFAULT_MODEL", "gpt-5.6-luna")

# Read from argv only when this file IS the program. Parsing it at import time
# makes the module explode under any other argv — a test runner's, another
# journey's — which is a silly way to lose a suite.
PROVIDER = (
    sys.argv[1]
    if __name__ == "__main__" and len(sys.argv) > 1
    else os.environ.get("JOURNEY_PROVIDER", "anthropic")
)
if PROVIDER not in PROVIDER_SPEC:
    raise SystemExit(
        f"unsupported provider {PROVIDER!r}; pick one of {list(PROVIDER_SPEC)}"
    )
CATALOG_PROVIDER, PILL_SUBSTRING = PROVIDER_SPEC[PROVIDER]


def _messages(s: DriverSession) -> int:
    return int(
        s.evaluate(
            'document.querySelectorAll("[data-testid^=tc-chat-message-]").length'
        )
        or 0
    )


def _has_error(s: DriverSession) -> bool:
    return bool(s.evaluate('!!document.querySelector("[data-testid*=error]")'))


def _await_reply(s: DriverSession, *, timeout_s: int = 120, minimum: int = 2) -> int:
    """Wait for a real streamed assistant reply, failing fast on an error surface.

    On timeout, ASK THE SERVER why. "no reply within 120s" is the symptom of a
    slow model, a dead worker and a run that failed on a 400 alike, and it sent
    a real diagnosis (`adaptive thinking is not supported on this model`) to a
    log nobody was reading. The run's own terminal status names it.
    """

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        assert not _has_error(s), (
            "an error surface ([data-testid*=error]) appeared during the run"
        )
        count = _messages(s)
        if count >= minimum:
            return count
        time.sleep(1)

    detail = ""
    try:
        conversation_id = wait_for_conversation_id(s, timeout_s=5)
        runs = runs_for_conversation(s, conversation_id)
        if runs:
            run = s.transport("GET", f"/v1/agent/runs/{runs[0]['run_id']}")
            detail = (
                f"; run {runs[0]['run_id'][:8]} status={run.get('status')!r} "
                f"safe_error={run.get('safe_error')!r}"
            )
    except Exception as exc:  # noqa: BLE001 — a diagnosis must not mask the failure
        detail = f"; (could not read the run back: {exc})"
    raise AssertionError(
        f"run did not stream an assistant reply within {timeout_s}s "
        f"(messages={_messages(s)}){detail}"
    )


def _anthropic_only(reason: str) -> None:
    require(PROVIDER == "anthropic", f"{reason} (provider pinned to {PROVIDER!r})")


# ── setup: the gate every phase below depends on ─────────────────────────────
def sign_in_and_key(s: DriverSession) -> None:
    key = load_env_key(PROVIDER)  # value never printed
    print(f"[first-run] provider={PROVIDER} key_len={len(key)} (value withheld)")
    s.sign_in_local()
    s.shot("sign-in-gate")
    s.ftue_add_key(PROVIDER, key)  # asserts first-run-composer appears


# ── phases ───────────────────────────────────────────────────────────────────
def fr1_state_b_composer(s: DriverSession) -> None:
    """The key connect reveals the State-B composer."""

    assert s.present("[data-testid=first-run-composer]"), "State-B composer not present"
    s.shot("byok-composer")


def fr2_catalog_marks_provider_configured(s: DriverSession) -> None:
    """The catalog, read THROUGH the app, marks the keyed provider configured."""

    catalog = s.transport("GET", "/v1/agent/models")
    assert catalog.get("default_model_id") == DEFAULT_MODEL_ID, (
        f"default_model_id={catalog.get('default_model_id')!r} != {DEFAULT_MODEL_ID!r}"
    )
    models = catalog.get("models", [])
    provider_models = [m for m in models if m.get("provider") == CATALOG_PROVIDER]
    assert provider_models, f"no {CATALOG_PROVIDER} models in catalog"
    configured = [m for m in provider_models if m.get("configured")]
    assert configured, (
        f"expected {CATALOG_PROVIDER} models configured=true after adding a key; "
        f"none of {len(provider_models)} are configured"
    )
    # `configured` means ONE thing: the provider has a usable credential, from
    # the deployment env or the caller's own BYOK key (`ModelCatalog._configured`).
    # There is no always-selectable carve-out, so no provider may report
    # configured=true without a key.
    #
    # The assertion this replaces demanded exactly that carve-out for
    # openrouter, citing an `ALWAYS_SELECTABLE` rule that exists nowhere in the
    # product — it survived only because nothing had run it in a while.
    unkeyed = [
        m
        for m in models
        if m.get("provider") not in {CATALOG_PROVIDER} and m.get("configured")
    ]
    assert not unkeyed, (
        "a provider with no key reports configured=true: "
        f"{sorted({m.get('provider') for m in unkeyed})}"
    )
    print(
        f"  {len(configured)}/{len(provider_models)} {CATALOG_PROVIDER} models "
        f"configured=true; default_model_id={DEFAULT_MODEL_ID}"
    )


def fr3_unkeyed_provider_stays_unconfigured(s: DriverSession) -> None:
    """OpenAI models are present but configured=false — the half that makes FR-4 real."""

    _anthropic_only("needs an Anthropic-only profile")
    models = s.transport("GET", "/v1/agent/models").get("models", [])
    assert models, "empty model catalog"
    openai_models = [m for m in models if m.get("provider") == "openai"]
    assert openai_models, "no openai models in the catalog to prove they are keyless"
    assert all(m.get("configured") is False for m in openai_models), (
        "with no OpenAI key, every openai model must be configured=false; got "
        f"{[m.get('configured') for m in openai_models]}"
    )
    print(f"  openai configured=false across {len(openai_models)} models")


def fr4_ftue_pill_preselects_the_keyed_provider(s: DriverSession) -> None:
    """The FTUE pill preselects a USABLE model, never the keyless deployment default.

    Before PR #260 `defaultSelectedModelId` returned a naive `models[0]`. The
    catalog leads with the deployment default, so an Anthropic-only user was
    preselected onto an UNUSABLE OpenAI model. The fix walks an explicit
    provider priority among configured, non-disabled models only
    (`desktopModelCatalog.ts` PROVIDER_PRIORITY).
    """

    pill = (s.model_pill() or "").strip()
    print(f"  FTUE model pill = {pill!r}")
    assert pill, "FTUE composer has no model pill text"
    if not PILL_SUBSTRING:
        return
    assert PILL_SUBSTRING.lower() in pill.lower(), (
        f"pill {pill!r} does not reflect provider {PROVIDER!r} "
        f"(expected substring {PILL_SUBSTRING!r})"
    )
    if PROVIDER == "anthropic":
        # Named by FAMILY, not by the current default's version string. This
        # asserted `gpt-5.4` and silently stopped testing anything the day the
        # deployment default moved to another OpenAI model.
        assert "gpt" not in pill.lower(), (
            "an Anthropic-only key must preselect a Claude model, never the "
            f"keyless OpenAI deployment default; got {pill!r}"
        )


def fr5_first_message_lands_on_its_run(s: DriverSession) -> None:
    """The FTUE first message binds a run instead of vanishing onto standby.

    Before PR #260 the FTUE created the conversation + run, but the hand-off
    into the shell discarded `{conversationId, runId}`, so the very first
    message landed on the empty standby composer. The fix threads
    FirstRunLaunchResult end-to-end and navigates the HashRouter to
    `#/convo/{conversationId}` BEFORE revealing the shell.
    """

    s.send_first_run_message("write a haiku about the sea")
    s.shot("ftue-sent")

    landed = False
    deadline = time.time() + 10
    while time.time() < deadline:
        if s.on_run():
            landed = True
            break
        time.sleep(0.5)
    assert landed, (
        "the first message did NOT land on a run within ~10s — it vanished onto "
        "the empty standby screen"
    )
    hash_ = s.evaluate("window.location.hash") or ""
    assert "#/convo/" in hash_, (
        f"expected the route to bind a conversation (#/convo/...), got {hash_!r}"
    )
    assert not s.present("[data-testid=run-empty-composer]"), (
        "run-empty-composer is still showing — the message sat on standby"
    )
    count = _await_reply(s)
    s.shot("ftue-reply")
    assert not _has_error(s), "error surface present after the run"
    print(f"  bound {hash_!r} and streamed a real reply ({count} messages)")


def fr6_new_chat_opens_a_fresh_cockpit(s: DriverSession) -> None:
    """Chats → New chat opens a clean slate, not the run you were already in.

    Before PR #260 `bootstrap.tsx`'s onNewChat called handleNavigate('run') and
    never cleared activeConversationId. Depends on FR-5 having bound a run —
    without one there is nothing for New-chat to fail to leave.
    """

    assert s.on_run(), "expected a bound run from FR-5 before testing New chat"
    s.open_destination("Chats")
    assert s.wait_for("[data-testid=chats-new-chat]"), (
        "Chats archive New-chat CTA (chats-new-chat) never appeared"
    )
    s.shot("chats-archive")

    s.click("[data-testid=chats-new-chat]")
    assert s.wait_for("[data-testid=run-empty-composer]"), (
        "New chat did not open the empty cockpit (run-empty-composer) — it "
        "likely re-opened the previously-bound run"
    )
    time.sleep(1)
    s.shot("new-chat-empty-cockpit")

    count = _messages(s)
    assert count == 0, (
        f"expected a clean transcript after New chat, found {count} messages — "
        "the previously-bound run was re-opened"
    )
    assert not s.evaluate(
        '(document.body.innerText||"").toUpperCase().includes("ACTIVE RUN")'
    ), (
        'the header still claims "ACTIVE RUN" after New chat — a fresh cockpit '
        "must be idle (STANDBY)"
    )


def main() -> int:
    plan = JourneyPlan("first-run")
    plan.boot(
        f"source · fresh · {PROVIDER}-only",
        lambda: DriverSession(name=f"first-run-{PROVIDER}"),
        setup=sign_in_and_key,
        phases=[
            ("FR-1", "key connect reveals the State-B composer", fr1_state_b_composer),
            (
                "FR-2",
                "the catalog marks the keyed provider configured",
                fr2_catalog_marks_provider_configured,
            ),
            (
                "FR-3",
                "an unkeyed provider stays configured=false",
                fr3_unkeyed_provider_stays_unconfigured,
            ),
            (
                "FR-4",
                "the FTUE pill preselects a usable model, not the keyless default",
                fr4_ftue_pill_preselects_the_keyed_provider,
            ),
            (
                "FR-5",
                "the first message lands on its run, not standby",
                fr5_first_message_lands_on_its_run,
            ),
            (
                "FR-6",
                "New chat opens a fresh cockpit, not the bound run",
                fr6_new_chat_opens_a_fresh_cockpit,
            ),
        ],
    )
    return plan.finish()


if __name__ == "__main__":
    raise SystemExit(main())

# chat-nav-model — Chats nav, FTUE hand-off, model preselect & Focus-only cockpit

Live-smoke drivers for the four Run-cockpit fixes shipped in **PR #260**
(`fix(desktop): Chats new-chat, FTUE first-message, and model preselect` +
`feat(run-cockpit): default to Focus mode + disable Studio for now`). Each
journey drives the **real** supervised 0xCopilot desktop app (Electron +
embedded Postgres + the three Python services) exactly as a user would —
clicking real testIds, reading the DOM the user sees, and making authenticated
facade calls _through_ the running app — then asserts the fix holds. Every
journey here was **verified live** in the session that authored PR #260.

> These are the bugs the old code shipped. A green run of the matching script
> proves the fix, not a mock: the app boots the branch's renderer, binds a real
> conversation, and streams a real model reply.

The four journeys are independent and each spawns its own driver in a throwaway
`userData` subdir (fresh first-run). Run any one:

```bash
python3 tools/desktop-journeys/chat-nav-model/new_chat.py
python3 tools/desktop-journeys/chat-nav-model/ftue_first_message.py
python3 tools/desktop-journeys/chat-nav-model/model_preselect.py
python3 tools/desktop-journeys/chat-nav-model/focus_only.py
```

Provider keys are read ONLY from `services/ai-backend/.env` via
`load_env_key("anthropic")` and are **never printed, logged, or committed** —
only lengths / HTTP status codes ever surface.

---

## A — Chats "New chat" opens a FRESH cockpit (`new_chat.py`)

**User story.** I'm in the middle of a run. I go to Chats and click "＋ New
chat". I expect a blank cockpit to start something new — not to be dropped back
into the run I was just looking at.

**The bug (before #260).** `bootstrap.tsx`'s `onNewChat` called
`handleNavigate('run')`, which **never cleared `activeConversationId`**. So
"New chat" simply re-opened the currently-bound run. There was no way to start
fresh from the archive.

**The fix (#260, `548d064f`).** `onNewChat` now calls `openNewRun` — the same
path as ⌘N and the palette's "New chat" command — which clears the bound
conversation before navigating, so Run mounts its empty composer.

| Step | Action                                                                                                   | Assertion                                                                                                                                                |
| ---- | -------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | Reach a **bound** run: sign in locally → FTUE add Anthropic key → send a message so a conversation binds | `s.on_run()` true (`[data-testid=tc-chat]` + ≥1 `[data-testid^=tc-chat-message-]`)                                                                       |
| 2    | Open the archive: `s.open_destination("Chats")`                                                          | Chats archive renders; New-chat CTA present `[data-testid=chats-new-chat]`                                                                               |
| 3    | Click New chat `[data-testid=chats-new-chat]`                                                            | —                                                                                                                                                        |
| 4    | Assert a FRESH empty cockpit                                                                             | `[data-testid=run-empty-composer]` **present**; `0` × `[data-testid^=tc-chat-message-]`; body text has **no** "ACTIVE RUN"; NOT the previously-bound run |

**testIds:** `chats-new-chat`, `run-empty-composer`, `tc-chat`,
`tc-chat-message-*`. **Nav rail:** `[aria-label="Chats"][data-destination="chats"]`.
**Expected outcome:** a blank Run cockpit ("STANDBY", not "ACTIVE RUN"), zero
transcript messages — a clean slate distinct from the run reached in step 1.

Fixed in PR #260.

---

## B — FTUE first message lands on its RUN, not an empty standby (`ftue_first_message.py`)

**User story.** Fresh install. I add my Anthropic key, type my very first ask in
the onboarding composer and hit send. I expect to watch that run start and
reply — not to be dumped on a blank "what should we run first?" screen with my
message gone.

**The bug (before #260).** The FTUE created the conversation + run, but the
hand-off into the shell **discarded the `{conversationId, runId}`**. The shell
mounted with no bound conversation, so the first message vanished onto the empty
standby composer.

**The fix (#260, `548d064f`).** `FirstRunLaunchResult` is threaded end-to-end;
the gate navigates the HashRouter to `#/convo/{conversationId}` **before**
revealing the shell, so Run binds the freshly-created run (mirrored on web via
`FirstRunSurfaceMount` + `App`).

| Step | Action                                                    | Assertion                                                                                          |
| ---- | --------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| 1    | `s.sign_in_local()` → `s.ftue_add_key("anthropic", …)`    | FTUE composer `[data-testid=first-run-composer]` appears; FTUE model pill shows a **Claude** model |
| 2    | `s.send_first_run_message("write a haiku about the sea")` | —                                                                                                  |
| 3    | Within ~10 s, assert it lands on the RUN                  | `s.on_run()` true; `location.hash` matches `#/convo/`; an assistant reply streams (≥2 messages)    |
| 4    | Assert it did **not** sit on standby                      | `[data-testid=run-empty-composer]` **absent**                                                      |

**testIds:** `first-run-composer`, `composer-textarea`, `tc-chat`,
`tc-chat-message-*`, `run-empty-composer`. **Route:** hash → `#/convo/{id}`.
**Expected outcome:** the first message appears in the transcript of a bound
conversation and a real Claude reply streams in; the standby composer is never
shown.

Fixed in PR #260.

---

## C — Fresh app + only an Anthropic key ⇒ preselect Claude, never keyless GPT-5.4 (`model_preselect.py`)

**User story.** I only added an Anthropic key. The composer should default to a
model I can actually run — a Claude model — not silently sit on "GPT-5.4 Mini",
which I have no key for and which would fail on send.

**The bug (before #260).** `defaultSelectedModelId`'s fallback returned a naive
`models[0]`. The catalog leads with the deployment default **`gpt-5.4-mini`**,
so an Anthropic-only user was preselected onto an **unusable** OpenAI model.

**The fix (#260, `548d064f`).** The fallback now walks an explicit provider
priority among **USABLE** (configured & not disabled) models only, returning
`""` when none qualifies:

> **OpenAI > Anthropic > OpenRouter > Gemini(google)** — first configured
> provider in that order wins; then the first usable model of _any_ other
> provider (covers local/Ollama); the keyless default is never picked.

(`apps/desktop/renderer/composer/desktopModelCatalog.ts`, `PROVIDER_PRIORITY`.)

| Step | Action                                                                                                         | Assertion                                                                                                                |
| ---- | -------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| 1    | Anthropic FTUE (`sign_in_local` → `ftue_add_key("anthropic", …)`) → send a message to bind + reach the cockpit | `s.on_run()` true                                                                                                        |
| 2    | Read the composer model (`s.model_pill()` / the run's model)                                                   | pill reflects a **Claude/Anthropic** model; **NOT** "GPT-5.4 Mini"                                                       |
| 3    | Catalog truth through the app: `transport("GET","/v1/agent/models")`                                           | every `provider=="openai"` model has `configured=false` (no key); ≥1 `provider=="anthropic"` model has `configured=true` |

**testIds / hooks:** `.atlas-model-pill` (composer pill text),
`/v1/agent/models` (`ModelCatalogModel.configured` / `.provider`).
**Expected outcome:** the preselected model is a Claude model the user can
actually run; the keyless `gpt-5.4-mini` default is never auto-selected while
only Anthropic is keyed.

Fixed in PR #260.

---

## D — The cockpit opens in Focus with no Studio toggle (`focus_only.py`)

**User story.** I open a run. It should be the clean Focus layout — I should not
see a Studio/Focus segmented toggle for a mode that's turned off.

**The change (#260, `7369f2cc`).** Studio is temporarily disabled behind a
single **revertable flag** — `STUDIO_ENABLED = false` in
`packages/chat-surface/src/destinations/run/useRunMode.ts`. With it off: the
default/persisted mode coerces to `"focus"`, the ⌘M toggle listener never
attaches, and `RunHeader` hides the segmented switcher. Flipping the flag back
to `true` restores Studio (and the switcher) in one line.

| Step | Action                                             | Assertion                                                                    |
| ---- | -------------------------------------------------- | ---------------------------------------------------------------------------- |
| 1    | Reach the cockpit: Anthropic FTUE + send a message | `s.on_run()` true                                                            |
| 2    | Read the resolved layout mode                      | `s.run_mode() == "focus"` (`[data-testid=thread-canvas][data-mode="focus"]`) |
| 3    | Assert the switcher is gone                        | `s.present("[data-testid=run-mode-switcher]")` is **false**                  |

**testIds:** `thread-canvas` (`data-mode`), `run-mode-switcher`.
**Expected outcome:** the run opens in Focus and no mode switcher renders.

**Revertable:** this is behind `STUDIO_ENABLED=false` in
`packages/chat-surface/src/destinations/run/useRunMode.ts`. When Studio is
re-enabled, this journey's step 3 assertion inverts (the switcher returns) — a
deliberate, one-line flip, not a regression.

Fixed in PR #260.

---

## BLOCKED-until

All four journeys are **fully assertable today** — they need only a staged
runtime and a real Anthropic key in `services/ai-backend/.env` (journeys A/B/C/D
all reach a live run via the Anthropic FTUE). With no key present, add-key and
the run-create hand-off cannot complete, so the scripts fail fast at
`ftue_add_key` rather than reporting a false pass. There is no partial/blocked
tail: the fixes are about navigation, hand-off, preselect, and layout, all of
which the live run exercises end-to-end.

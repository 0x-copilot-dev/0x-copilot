# provider-key-byok — first-run BYOK + model catalog

**User story.** A brand-new user opens the freshly-installed 0xCopilot desktop
app. There are **no keys configured**. They choose to use the app locally with no
account, hit the First-Run gate ("First, give it a model."), bring their own
provider key, and immediately send their first message — and it just runs against
their key. This set proves that whole path end-to-end against the **real**
supervised stack (Electron + embedded Postgres + the three Python services), not a
mock: every step is a real DOM click / fill or an authenticated facade call made
_through_ the running app.

Two journeys, one script (`byok_first_run.py`, parametrized by provider):

- **J-BYOK-OPENAI** — add an OpenAI key → `gpt-5.4-mini` (the deployment default)
  becomes runnable and is what the composer preselects.
- **J-BYOK-ANTHROPIC** — add **only** an Anthropic key → Claude models become
  runnable and the composer preselects a **Claude** model, _not_ `gpt-5.4-mini`.

```bash
# from the repo root (see ../README.md for one-time build/stage + .env key setup)
python3 tools/desktop-journeys/provider-key-byok/byok_first_run.py            # anthropic (default)
python3 tools/desktop-journeys/provider-key-byok/byok_first_run.py openai     # openai
```

The key is read only from `services/ai-backend/.env` via `load_env_key(provider)`
and is **never printed, logged, or committed** — only its length and HTTP status
codes ever surface.

---

## Catalog / "configured" facts asserted

`GET /v1/agent/models` (read through the app as the signed-in user) returns a
`default_model_id` plus a `models[]` list; each model carries `provider` and a
`configured` boolean. The rules the scripts pin (source of truth:
`services/ai-backend/src/agent_runtime/api/model_catalog.py`):

| Fact                                                                                                      | Where enforced                                          |
| --------------------------------------------------------------------------------------------------------- | ------------------------------------------------------- |
| `default_model_id == "gpt-5.4-mini"`                                                                      | `settings.default_model` (deployment default)           |
| `configured` = **env keys ∪ the user's BYOK key ∪ ALWAYS_SELECTABLE**                                     | `ModelCatalog._configured`                              |
| `ALWAYS_SELECTABLE_PROVIDERS == {"openrouter"}` — OpenRouter reads `configured=true` even with **no** key | `ModelCatalog.ALWAYS_SELECTABLE_PROVIDERS`              |
| `openai` / `anthropic` / `gemini` need a **real** key to be `configured`                                  | `ModelCatalog._configured` (no ALWAYS_SELECTABLE entry) |
| After adding an OpenAI key → all `openai` models (incl. `gpt-5.4-mini`) `configured=true`                 | BYOK provider added to `user_key_providers`             |
| After adding only an Anthropic key → all `anthropic` (Claude) models `configured=true`                    | same                                                    |

The FTUE composer **preselect** walks provider priority
**OpenAI > Anthropic > OpenRouter > Gemini** among **configured** models and never
selects a keyless default. So an Anthropic-only key preselects a Claude model
(not `gpt-5.4-mini`), while an OpenAI key preselects `gpt-5.4-mini`.

> **Preselect note.** The **FTUE composer** preselect was verified correct here.
> The separate **Run-cockpit STANDBY composer** had its own preselect bug (it
> could show the keyless default even when only a non-OpenAI key was configured);
> that was fixed in **PR #260**. This set exercises the FTUE composer path.

---

## J-BYOK-OPENAI

**Story.** Fresh install, no keys → use locally → FTUE → add an OpenAI key → send
"hi" → a real GPT reply streams in.

| #   | Step                                     | Action (testId)                                                       | Expected                                                                   |
| --- | ---------------------------------------- | --------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| 1   | Sign-in gate, pick the no-account option | click `sign-in-button` ("Use locally, no account")                    | FTUE gate renders (H1 "First, give it a model.")                           |
| 2   | Open the BYOK add-key form               | click `first-run-add-key` → `first-run-keyform` appears               | inline KeyForm with provider tri-toggle + `first-run-key-input`            |
| 3   | Pick **OpenAI**, paste key, Connect      | radio "OpenAI" → fill `first-run-key-input` → `first-run-key-connect` | live-validates, flips to `first-run-composer` (State B)                    |
| 4   | Read the catalog through the app         | `transport("GET","/v1/agent/models")`                                 | `default_model_id=="gpt-5.4-mini"`; every `openai` model `configured=true` |
| 5   | Model pill reflects OpenAI               | read `.atlas-model-pill`                                              | pill text contains `gpt` (the preselected `gpt-5.4-mini`)                  |
| 6   | Send the first message                   | fill `composer-textarea` "hi" → `button[aria-label="Send message"]`   | run streams a real assistant reply                                         |
| 7   | Assistant reply present, no error        | poll `[data-testid^=tc-chat-message-]`                                | ≥ 2 `tc-chat-message-*` and **no** `[data-testid*=error]`                  |

**Expected outcome:** `ALL PASS — J-BYOK-OPENAI`, exit 0. Screenshots
`01-sign-in-gate … NN-byok-reply` under `runs/byok-openai/`.

---

## J-BYOK-ANTHROPIC

**Story.** Same fresh path, but the user brings only an **Anthropic** key. The
composer must preselect a **Claude** model — proving the preselect prefers a
configured provider over the keyless deployment default.

| #   | Step                                               | Action (testId)                                                          | Expected                                                                                                                          |
| --- | -------------------------------------------------- | ------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Sign-in gate, pick the no-account option           | click `sign-in-button`                                                   | FTUE gate renders                                                                                                                 |
| 2   | Open the BYOK add-key form                         | click `first-run-add-key` → `first-run-keyform`                          | inline KeyForm (Anthropic is the default provider toggle)                                                                         |
| 3   | Pick **Anthropic**, paste key, Connect             | radio "Anthropic" → fill `first-run-key-input` → `first-run-key-connect` | live-validates, flips to `first-run-composer`                                                                                     |
| 4   | Read the catalog through the app                   | `transport("GET","/v1/agent/models")`                                    | `default_model_id=="gpt-5.4-mini"`; every `anthropic` model `configured=true`; `openai`/`gemini` stay `configured=false` (no key) |
| 5   | Model pill reflects Anthropic, **not** the default | read `.atlas-model-pill`                                                 | pill text contains `claude` and **not** `gpt-5.4` (preselect avoided the keyless default)                                         |
| 6   | Send the first message                             | fill `composer-textarea` "hi" → Send                                     | run streams a real assistant reply                                                                                                |
| 7   | Assistant reply present, no error                  | poll `[data-testid^=tc-chat-message-]`                                   | ≥ 2 `tc-chat-message-*` and **no** `[data-testid*=error]`                                                                         |

**Expected outcome:** `ALL PASS — J-BYOK-ANTHROPIC`, exit 0. Screenshots under
`runs/byok-anthropic/`.

---

## testIds asserted

| testId / selector                   | Surface                            |
| ----------------------------------- | ---------------------------------- |
| `sign-in-button`                    | Sign-in gate ("Use locally…")      |
| `first-run-add-key`                 | FTUE BYOK card ("Add a key")       |
| `first-run-keyform`                 | inline KeyForm                     |
| `first-run-key-input`               | key password field                 |
| `first-run-key-connect`             | Connect / validate button          |
| `[role=radio]` by label             | provider tri-toggle                |
| `first-run-composer`                | State-B first-run composer         |
| `.atlas-model-pill`                 | composer model pill                |
| `composer-textarea`                 | message input                      |
| `button[aria-label="Send message"]` | send                               |
| `[data-testid^=tc-chat-message-]`   | streamed chat messages             |
| `[data-testid*=error]`              | any error surface (must be absent) |

Catalog assertions go through `transport("GET","/v1/agent/models")` — an
authenticated facade call made _through_ the running app, so a green run proves the
real per-user BYOK → catalog wiring, not a fixture.

---

## BLOCKED-until

- **Both journeys need a real key** in `services/ai-backend/.env`
  (`OPENAI_API_KEY` / `ANTHROPIC_API_KEY`). Without it `load_env_key` exits early —
  the run-create + live-validate steps cannot pass with a placeholder, by design.
- **Staged runtime required.** The catalog + run path run the real Python services;
  re-stage after any `services/*` change (`node tools/desktop-runtime/stage.mjs
--platform … --arch …` or `make desktop-install`) or the journey runs stale
  backend code. Frontend-only changes just need
  `npm run build --workspace @0x-copilot/desktop`.
- **Network to the provider.** Step 3's live key-validation and step 6's run both
  call the real provider API; an offline host fails these (not a product bug).

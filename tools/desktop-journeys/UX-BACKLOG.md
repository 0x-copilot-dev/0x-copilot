# Desktop run cockpit UX backlog

Reported product/design issues and their implementation/verification record.
Entries remain here after resolution so future desktop journeys have an
evidence-backed product decision to test against.

## B-05 — Bypass control is offered on a fresh install, but the module says "default off"

- **Status:** Open — needs a product decision, not a code fix
- **Observed:** A fresh install reports `filesystem_bypass_enabled: true`, so the
  execution-mode pill is enabled and Bypass is selectable with no persisted row.
- **The two sides.**
  [`filesystem_bypass.py:177`](../../services/ai-backend/src/agent_runtime/execution/filesystem_bypass.py:177)
  sets `DEFAULT_FILESYSTEM_BYPASS_OFFERED = True`, and the comment above it
  explains why: when the pydantic default and the raw-JSONB read disagreed, the
  API advertised a control that every run then sealed off — "a pill the user
  could select that the server then silently ignored." Setting it `True` made
  the two layers agree. The same module's **docstring** still describes tier 1
  as "a Settings switch … default **off**."
- **Why it matters:** both statements are load-bearing. `True` is what makes the
  API and the run agree; "default off" is what the workspace-bypass journeys
  encode. Whichever is wrong, one of them is currently lying to a reader.
- **Decide, then align:** resolve which is intended before changing either side.
  `ABSENT is not FALSE` (line 174) must survive whichever way it goes — an
  explicit operator `False` has to keep working.
- **Coverage:** [workspace_bypass.py](./workspace_bypass.py) — the default and
  enforce lanes both exercise the pill.

## B-01 — Run receipt launcher overwhelms ordinary Studio runs

- **Status:** Resolved — verified in the freshly packed installed desktop
- **Evidence:** `codex-clipboard-aa7a0242-dcc6-488d-b730-796c379be17f.png`
- **Observed:** A large “Run receipt ready” launcher is shown for an ordinary
  completed subagent run, alongside a “Run receipt” tab.
- **Decision / implementation:** An ordinary chat, tool, or subagent run no
  longer mounts a receipt launcher or tab. Its receipt remains durable and
  exportable; the cockpit offers a single compact **Run receipt · Review**
  control only when the ledger has consequential effects, gates, or a promoted
  artifact. A tab is created only after that explicit review action.
- **Coverage:** `RunDestination.surfacesV2.test.tsx` and the rich-chat journey
  both assert that an ordinary subagent run has no receipt launcher.

## B-02 — Parallel-subagent fleet card is too large and repeats low-value copy

- **Status:** Resolved — verified in the freshly packed installed desktop
- **Evidence:** `codex-clipboard-fb3a9cdd-f110-4b7e-a8ae-66dffa4b8719.png`
- **Observed:** The inline “Dispatched 2 subagents in parallel” card consumes a
  large portion of the transcript after work completes.
- **Decision / implementation:** A running fleet starts expanded; on its
  terminal transition it folds to a compact title/count/elapsed summary. Its
  semantic header button exposes child details, and preserves a later manual
  choice. Both redundant explanatory sentences were removed.
- **Coverage:** `SubagentFleetCard.test.tsx` and R9 of the rich-chat journey
  exercise pointer, Space, and Enter on the terminal card.

## B-03 — Run header is vertically cramped and clips its hierarchy

- **Status:** Resolved — verified by desktop build and header style contract
- **Evidence:** `codex-clipboard-108e51f0-a17d-46ab-87b7-3b991faacb86.png`
- **Observed:** The header has insufficient vertical padding. The “ACTIVE RUN”
  label and model/run name (`claude-haiku-4-5`) crowd each other and appear to
  touch or clip against the header borders.
- **Decision / implementation:** The header now has a 58px minimum height,
  8px/20px vertical/horizontal padding, a 40px avatar, and explicit 14px/20px
  kicker/title line-heights. It no longer relies on a 38px fixed-height row.
- **Coverage:** `RunHeader.test.tsx` asserts the dimension/line-height contract;
  the refreshed computed-style harness measures this deliberate change.

## B-04 — Nested activity card wastes horizontal space and clips its status

- **Status:** Resolved — verified by full chat-surface suite and style contract
- **Evidence:** `codex-clipboard-da7c0ff4-e012-434d-b0cd-ce800a7fb138.png`
- **Observed:** The terminal `COMPLETED` status does not fit cleanly in the row.
  The card is also inset beneath a separate left-hand timeline dot and rail.
- **Product concern:** The card already includes a status icon, title, border,
  and nested activity markers. The outer dot/rail repeats hierarchy while
  reducing the width available to actual task content.
- **Decision / implementation:** The activity card no longer uses its
  `data-depth` margin, so its border owns the full available row width. Tool
  timelines remain nested only inside the card’s expanded detail surface. Their
  content track is now shrinkable and their terminal status has a bounded,
  ellipsized right-aligned track, preventing `COMPLETED` from overflowing.
- **Coverage:** the full chat-surface suite covers native detail disclosure;
  the layout contract makes the card own the available row width and bounds
  the terminal-status track rather than allowing it to overflow.

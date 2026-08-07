# Consolidation: 64 journey scripts → 9 merged journeys

**Status: all 9 merged journeys built. No original has been deleted yet.** The old
per-set directories still run exactly as before; the merged files sit beside
them until all nine exist.

## Why

One supervised boot costs initdb + migrations + three service starts. The suite
was 64 scripts and therefore 64 boots, which is most of its wall clock — and
[RUN-RESULTS.md](./RUN-RESULTS.md) records that a full sweep has never produced
a trustworthy result. Journeys are now grouped by **what they need from the
machine**, and each group shares one boot.

## What makes this safe

Sharing a boot must not cost what a script-per-claim bought: 64 independent
verdicts. A naive concatenation has ONE, aborts at the first assertion, and lets
one absent prerequisite hide every later claim.

So a boot runs **phases** (`JourneyPlan` in [\_lib.py](./_lib.py)). Each phase is
isolated, records its own outcome (`passed` / `failed` / `blocked` / `skipped`),
and the next one runs regardless. The file's exit code is the aggregate —
severity ordered, so a real defect outranks a missing product capability, which
outranks a missing local prerequisite:

| Exit | Meaning                                                   |
| ---- | --------------------------------------------------------- |
| `0`  | every phase ran and passed                                |
| `1`  | at least one phase failed                                 |
| `2`  | no failures, but a declared product capability is absent  |
| `3`  | no failures or blocks, but a local prerequisite is absent |

A run with even one skipped phase is never `0`, because the file did not prove
what its name claims.

## The grouping axes

Only these four things force a **separate boot** — they are fixed when the
supervisor launches and cannot change mid-session:

| Axis    | Values                           | Set by                                                        |
| ------- | -------------------------------- | ------------------------------------------------------------- |
| Target  | `source` / `installed-payload`   | `COPILOT_HOME` + the staged artifact (`resolve_copilot_home`) |
| Profile | `fresh` / `reuse`                | the userData subdir; `reuse` = a hand-connected OAuth server  |
| Lane    | default / enforce                | `OPERATION_GATEWAY_MODE` + `WORKSPACE_EFFECT_MODE`            |
| Model   | live BYOK / `RUNTIME_FAKE_MODEL` | process env before boot                                       |

Everything else is theme, not constraint.

## The nine

| Journey                                                  | Boot class               | Folds in                                                                                                             | Status                |
| -------------------------------------------------------- | ------------------------ | -------------------------------------------------------------------------------------------------------------------- | --------------------- |
| [first_run.py](./first_run.py)                           | source · fresh           | provider-key-byok/byok_first_run, chat-nav-model/{model_preselect, ftue_first_message, new_chat}                     | ✅ 6 phases           |
| [transcript_rendering.py](./transcript_rendering.py)     | source · fresh           | chat-rich-cards/rich_chat, focus-mode, focus-inline-artifacts, turn-interleaving ×5, transcript-density, timeline ×2 | ✅ 18 phases          |
| [composer_and_budgets.py](./composer_and_budgets.py)     | source · fresh           | composer-tools, agent-todos/todo_panel, chat-rich-cards/{tool_budget_setting, budget_overrun, declined_capability}   | ✅ 7 phases           |
| [shell_and_projects.py](./shell_and_projects.py)         | source · fresh           | shell-overflow ×2, projects-filing ×2                                                                                | ✅ 11 phases          |
| [workspace_consent.py](./workspace_consent.py)           | source · fresh · default | filesystem-access/{FS1, downloads, jA, jC, FS2, jG, jE}, agent-todos/todos_with_gate                                 | ✅ 9 phases           |
| [workspace_bypass.py](./workspace_bypass.py)             | source · default+enforce | filesystem-access/{jD, jH, jK, jI, attached_folder_stops_asking, jB}                                                 | ✅ 11 phases, 2 boots |
| [artifacts_and_surfaces.py](./artifacts_and_surfaces.py) | source · fresh           | generative-workflows/{g0, g2a, g2b, g2c, g2d}, surface-colour, surface-follow-live, surface-floor                    | ✅ 9 phases           |
| [mcp_connected.py](./mcp_connected.py)                   | source · **reuse**       | filesystem-access/jF_linear_mcp, connectors ×2, write-gate-inline                                                    | ✅ 5 phases           |
| [installed_payload.py](./installed_payload.py)           | installed · fresh+reuse  | installed-payload smoke, g3–g10, jJ                                                                                  | ✅ 10 phases, 2 boots |

## Four originals are NOT folded in

**Two because they assert nothing:**

- **`run-timeline-persistence/catch_gap.py`** documents its own expected result
  on a fixed build as _"no gap observed (exit 0, nothing to photograph)"_. It
  exists to photograph a PRE-fix defect. `TR-17`'s 50ms sampler is the real
  proof and is strictly stronger.
- **`run-timeline-persistence/sources_probe.py`** says of itself _"Prints
  findings; does not assert. Delete once the bug is understood and covered by a
  real journey."_ Its three probes survive as diagnostic output inside `TR-18`.

**Two because the work is not done:** `generative-workflows/g1_markdown_lifecycle.py`
(1152 lines) and `g2_csv_lifecycle.py` (1027 lines) are still standalone
`installed-payload` journeys. Their _helpers_ were lifted into
[\_workspace_lib.py](./_workspace_lib.py) and
[artifacts_and_surfaces.py](./artifacts_and_surfaces.py), but their own
end-to-end narratives were not folded into `installed_payload.py`. Both remain
runnable exactly as before. Folding them is the one piece of this migration
left: they belong as `IP-11` (Markdown lifecycle) and `IP-12` (CSV lifecycle),
following the same `_matrix`-style spine the G3-G10 phases use.

So the count is **62 of 64 folded**, not 64.

## One contradiction surfaced, deliberately not resolved

`jD_bypass` and `jH_bypass_demo` shipped encoding OPPOSITE expectations of a
fresh install, which [RUN-RESULTS.md](./RUN-RESULTS.md) already flags as NEEDS A
PRODUCT DECISION:

- `agent_runtime/execution/filesystem_bypass.py:177` sets
  `DEFAULT_FILESYSTEM_BYPASS_OFFERED = True` — what `jH` encodes ("the master
  switch is ON out of the box, and it must be").
- The same module's docstring says the master switch is "default **off**" —
  what `jD` encodes (a fresh install shows a DISABLED Manual pill).

They cannot both pass, so merging them by asserting either side would pick a
winner by fiat. `WB-3` asserts the invariant true under EITHER default — **the
options the pill offers must agree with the master switch it reads** — and
records the observed default in `runs/…/wb3-master-switch.json`. Once the
product decides, tighten `WB-3` to the chosen side.

## Shared code that moved

Three helper sets were private to one journey and imported ACROSS set
boundaries — `focus-inline-artifacts` reached into `generative-workflows`, and
`filesystem-access/_fs_journey_lib` reached there too. They now live where every
journey can see them:

- **[\_lib.py](./_lib.py)** gained `preflight_staged_runtime`, `byok_provider`,
  `runs_for_conversation`, `wait_for_conversation_id`, `wait_for_new_run`,
  `wait_for_terminal_run`, `assert_no_plaintext_secret`, plus
  `DriverSession.send()` (every original assumed it owned the first message in
  its boot, so all 64 used the FTUE composer; grouped phases need one that also
  works in the run cockpit) and automatic clearing of the previous run's
  screenshots.
- **[\_workspace_lib.py](./_workspace_lib.py)** is the former
  `filesystem-access/_fs_journey_lib.py` plus the macOS native-dialog
  automation it used to import from `g2_csv_lifecycle`.

## Finishing the last two

Each merged file is: a docstring naming what it folds in and why the phases are
ordered, the helper functions **lifted verbatim** from the originals (use
`ast`-based extraction with renaming rather than retyping — a retyped assertion
is a changed assertion), then a phase spine and `JourneyPlan.boot(...)`.

Rules learned building the first five:

1. **Order phases by the state they consume**, and say so in the docstring. A
   phase needing the virgin FTUE composer must precede anything that sends.
2. **Dependent phases declare what they need.** `require(STATE.get(k), …)` so a
   missing predecessor skips rather than fails with a confusing error.
3. **Model-driven shape is `blocked`, not `failed`.** If the model declines to
   call web search or skips reasoning, the shape under test never occurred.
4. **A phase that changes persisted settings runs last** (see `CB-7`), or it
   silently rewrites an earlier phase's premise.

Delete an original only once its claims live somewhere else, and rewrite
[README.md](./README.md)'s Layout and Running sections in the same commit.

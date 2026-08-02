# S1 / S2 spikes — the measured floor, and the fastest path to it

Seven parallel spikes, 2026-08-02. **S1 built and ran a lean runtime and measured it.
S2 measured what the shipped product actually executes.** Where a number below is an
estimate rather than a measurement, it says so — this programme has had five headline
numbers turn out wrong, all from reasoning instead of running.

---

## 1. S1-A — the skeleton exists, runs, and is **515 LOC**

Not an estimate. A real `create_deep_agent` graph (nodes model/tools + PatchToolCalls
and TodoList middleware, 10 tools bound free from the library), a real tool dispatch, a
streamed `model_delta`, typed envelopes with monotonic `sequence_no`, a live subscriber,
and `after_sequence` resume. `spike/demo_run.py` exits 0 repeatably with no API key,
emitting 12 events, seq 1..12, no gaps; `replay(after=3)` returns 9.

| Component            |     LOC |
| -------------------- | ------: |
| event adapter        |     125 |
| event contract       |     123 |
| run executor         |      96 |
| store                |      63 |
| replay + stream      |      54 |
| agent builder        |      43 |
| tools                |      11 |
| **runtime subtotal** | **515** |

**The same five concerns in the service today: 16,553 LOC** (`deep_agent_builder` 849 +
`stream_*` 4,493 + `schemas/events.py` 2,693 + `runtime_adapters/in_memory` 8,518).
A 32× gap — but the honest reading is that those files also carry subagents, approvals,
MCP, redaction and tenancy, which the spike has **none** of. That is what S1-B/C/D size.

**What the 515 does not have** (from its own caveats): no auth, no tenancy, no FastAPI/SSE
endpoint, no durability or checkpointer, no interrupts → no approvals/HITL/cancel, no
queue or worker, no MCP, no skills/memory, no subagent attribution (it ignores the chunk
`ns`, so a subagent's deltas would be misattributed), no reasoning/citations/usage/
redaction/coalescing, one bare `except`, zero tests.

## 2. S1-B/C/D — what a lean version of the rest costs

Each read today's source and classified every capability, **verifying every
"framework provides it" claim by importing or running the installed package**.

| Scope                                 |   Today | Framework provides                                                                                                         | **Lean rebuild** | Droppable |
| ------------------------------------- | ------: | -------------------------------------------------------------------------------------------------------------------------- | ---------------: | --------: |
| Approvals / HITL / subagents / cancel |  14,550 | `interrupt`/`Command(resume)`/checkpointer, `HumanInTheLoopMiddleware` (485), `SubAgent`+`AsyncSubAgentMiddleware` (1,824) |       **~1,890** |    ~4,000 |
| Tools + MCP + PDP/PEP + errors        | ~36,100 | `langchain_mcp_adapters` 0.3.1, `mcp` 1.29 OAuth, `ToolCallLimit`/`ToolError` middleware (~3,700)                          |       **~2,200** |   ~30,000 |
| Persistence + queue + worker + SSE    | ~60,300 | checkpointing **and mid-run crash recovery**                                                                               |       **~2,350** |   ~46,000 |

Two results verified **by execution**, not by name:

- **Crash recovery is free.** A probe crashed in node B, opened a _new_ process with a
  _new_ `AsyncSqliteSaver` on the same file, and `ainvoke(None, cfg)` resumed at B
  without re-running A.
- **Cancellation is not.** Pregel has no cancel/stop — that one is genuinely ours.

Also surfaced: **two parallel MCP implementations ship today and only the legacy
`call_mcp_tool` gateway is wired**; and a real bug — the worker lease is 60s and never
renewed, so a run longer than 60s can be re-claimed by a second worker on Postgres.

### The floor

|                                          |        LOC |
| ---------------------------------------- | ---------: |
| Skeleton spine (**measured, runs**)      |        515 |
| Approvals / subagents / cancel (est.)    |      1,890 |
| Tools / MCP / policy / errors (est.)     |      2,200 |
| Persistence / queue / SSE / store (est.) |      2,350 |
| **Sum**                                  | **~6,955** |

Nobody scoped the production-fidelity event adapter (~50 event types, subagent
attribution, coalescing, redaction — `stream_*` is 4,493 today), nor tenancy/auth, the
HTTP surface, or observability. Add those and tests, and a realistic lean runtime is
**~10–15k of `src`, perhaps ~20–25k with everything nobody scoped.**

**So 50k is not aggressive. It is 2–4× the honest floor.** (My earlier "~100k floor" was
wrong, and so was my "~25–35k" — both were anchored on subtracting from today's
architecture rather than measuring what the job costs.)

## 3. S2-C — **87,632 LOC (28.3%) is dark in every shipped configuration**

The single most actionable finding of the entire programme, and it needs **no rewrite**.

Verified by running the real resolvers against an empty env: all 10 E2 rollout lanes
resolve `off`; all 12 F1–F12 features resolve OFF because
`RunControlAssignment.safe_active_v1()` returns an all-OFF `FeatureModeSet`, and
`RUNTIME_HARNESS_RELEASE_CONFIG_PATH` — the only thing that could override it — **is set
nowhere in the repo**. Checked against hosted defaults, `env_example`,
`docker-compose`, self-host, **and the packaged desktop**.

|    LOC | Dark block                                 |
| -----: | ------------------------------------------ |
| 10,961 | F1 harness-quality + evaluation projection |
| 10,952 | F6 batch concurrency                       |
|  9,494 | remote sandbox                             |
|  8,037 | F3 capability discovery                    |
|  5,971 | the E2 rollout machinery itself            |
|  4,828 | workspace overlay/commit modes             |
|  4,692 | operation-gateway mode                     |
|  4,531 | effect stager/commit modes                 |
|  3,957 | 8 worker jobs behind `*_ENABLED=false`     |
|  3,908 | F5 context budgeting                       |

**This reconciles with the boundary audit rather than contradicting it.** That audit
called `concurrency/` and `discovery/` RUNTIME — architecturally correct. The flag census
says they never execute. Both are true, and together they license deletion: _belongs here
in principle, has never run in practice._

## 4. S2-B — a real desktop run touches ~1/6 of the service

Measured with `coverage.py` inside the real supervised uvicorn across **three live
packaged-desktop boots** (prod posture, embedded Postgres, file store, real BYOK model
calls), including a live `web_search` and a 2-agent fleet.

- **9,289 of 59,801 function-body statements executed = 15.5%**
- **458 of 799 files (150,291 LOC, 50.9%) executed zero body statements**; 112 files
  (46,139 LOC) were never imported at all
- `harness_quality` 0.2% · `effects` 1.6% · `capabilities` 8.0% · `runtime_adapters` 8.9%

Honest caveat the spike insisted on: **not-exercised ≠ dead.** Part of the inert half is
config-excluded (the Postgres tree can't run when desktop uses the file store; MCP paths
need an OAuth connect the harness cannot perform). Naive _line_ coverage reads 45.1% and
is import-inflated — don't quote it.

## 5. S2-A — topology is **not** the lever (this kills a previous recommendation)

I had proposed "go desktop-only → delete the server topology, −25k." Measured: **88% of
ai-backend is on the desktop's single-process path.** Server-only is **7,345 LOC (2.4%)**.
Even counting everything unreachable from either entry (29,442) plus the whole Postgres
tree (15,470), a desktop-only product sheds at most **~52k of 309k**. Desktop is one
process: `uvicorn runtime_api.app` with `RUNTIME_START_IN_PROCESS_WORKER=true`,
`RUNTIME_STORE_BACKEND=file`, `ENTERPRISE_DEPLOYMENT_PROFILE=single_user_desktop`.

## 6. What to do — subtract first, then strangle

I previously said you cannot delete your way to 50k and the ladder stops at ~107k. **The
flag census changes that**, and the two strategies compose:

1. **Delete the dark (~87.6k), zero observable behaviour change.** Nothing shipped can
   turn it on. Start with the biggest self-contained blocks — F1 eval/harness_quality,
   F6 batch concurrency, remote sandbox, F3 discovery, E2 rollout.
2. **Delete the dead (~9k)** — after fixing the `orphans.py` `__init__` blind spot that
   hid it.
3. **Drop two of three store backends (~15k)** — S1-D's `CAN_DROP`, and it converges with
   the base-extraction already in flight.
4. **Then strangle the remainder.** Every deletion above shrinks the porting surface, so
   doing them first makes the strangler dramatically cheaper.

Subtraction plausibly reaches **~150–180k**; the strangler takes it to **~15–25k**.
Neither alone gets to 50k; in that order, both do — and step 1 is by far the best
value-to-risk in the whole programme.

## 7. Confidence

| Claim                                | Basis                                                       |
| ------------------------------------ | ----------------------------------------------------------- |
| 515-LOC skeleton runs                | **executed**, exit 0, repeatable                            |
| crash recovery is framework-provided | **executed** (crash + resume probe)                         |
| Pregel has no cancel                 | **verified** in the installed package                       |
| 87,632 dark                          | **executed** the real resolvers under empty env             |
| 15.5% body-statement coverage        | **measured** over 3 live desktop boots                      |
| server-only = 7,345                  | static AST closure (upper bound) + 2 real boots             |
| lean rebuild ~1.9k / ~2.2k / ~2.35k  | **estimates**, per-row and additive                         |
| ~10–15k realistic lean runtime       | **inference** from the above; the least certain number here |

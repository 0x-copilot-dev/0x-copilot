# Local Search Extraction — PRD

Status: DRAFT for review · Target service: `services/ai-backend` (desktop-first)
Related: `docs/plan/mcp-tooling-program/PRD.md` §8.1 (the `response_format` bug, fixed separately)

---

## 1. Problem

`web_search` today is discovery-only, and that is the quality ceiling.

`WebSearchToolRegistry` (`runtime_worker/dependencies.py:143-155`) queries DuckDuckGo via `ddgs`
with `MAX_RESULTS = 4` (`:96`) and returns:

```python
results = [{"snippet": r["body"], "title": r["title"], "link": r["href"]} for r in raw_results]
return results, raw_results          # (content, artifact)
```

Two independent problems live in that one line.

**1a — the artifact is a rename-duplicate.** `snippet` _is_ `body`, `link` _is_ `href`. DuckDuckGo
text results carry only `title` / `href` / `body`, so the artifact adds no metadata, no redirect
URLs, nothing. Combined with the wrapper bug (a middle layer dropped `response_format`, so both
halves were stringified into `ToolMessage.content`), **every search paid roughly twice for the same
bytes**. That is being fixed separately in the mcp-tooling-program; this PRD assumes it lands.

**1b — the real ceiling: a snippet is not an answer.** Four DuckDuckGo snippets are ~1-3 sentences
each, chosen by a general-purpose ranker for a _human scanning a results page_, not for an agent
answering a specific question. The published failure mode is exactly ours: _"snippets have a habit
of cutting off right before the part you need — where a snippet might tell you a regulation
changed, the full page tells you what changed, when it took effect, and what the old wording said."_

The agent's only recovery is to search again with different words. That is the expensive loop: more
searches, more tokens, worse answers.

### Non-goal

This PRD does **not** propose changing the _discovery_ engine. DuckDuckGo is free, unmetered, and
adequate at "which four pages are relevant". The gap is everything after that.

---

## 2. Why not simply buy a search API

The obvious move — Tavily, Exa, Firecrawl — is structurally wrong **for this product**, not merely
expensive.

| Constraint                            | Consequence                                                                                                                                                            |
| ------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Distributed desktop app**           | A single shared API key is consumed by the whole user base at once. There is no "our server" to meter it on.                                                           |
| **BYOK already**                      | Users supply an LLM key. Requiring a _second_ key for search is real onboarding friction for a feature they expect to just work.                                       |
| **Free tiers are small and unstable** | Tavily ~1,000/mo, Exa ~1,000/mo — per key. And **Brave eliminated its free tier in February 2026**, which is the evidence that free-tier-as-strategy does not survive. |
| **Local-first positioning**           | A hosted search API sees every query the user's agent makes. Local fetch does not.                                                                                     |

The paid APIs are selling **extraction and relevance ranking**, not link discovery. We can perform
both locally, on the user's own machine, unmetered.

---

## 3. What we already have

The expensive parts are already in the codebase. This is a composition problem.

| Capability                     | Where                                                                                                                  | Status  |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------- | ------- |
| Page fetch (incl. JS-rendered) | `agent_runtime/capabilities/browser/` (`desktop_browser_provider.py`)                                                  | present |
| Local embeddings               | `build_embeddings_model` (`execution/deep_agent_builder.py`), `/v1/llm/embed` (`runtime_api/http/llm_embed_routes.py`) | present |
| HTML parsing                   | `lxml==6.1.1`                                                                                                          | pinned  |
| Search discovery               | `ddgs` via `WebSearchToolRegistry`                                                                                     | present |

One new dependency is expected: a main-content extractor (`trafilatura` — free, pure-Python,
no service). Everything else is wiring.

---

## 4. Proposed design

```
query
  ↓
DDG search (free, existing)            → N candidate URLs + snippets
  ↓
fetch pages locally, in parallel       → raw HTML            [browser capability]
  ↓
extract main content                   → clean article text  [trafilatura/lxml]
  ↓
chunk + rank passages against query    → top-K passages      [local embeddings]
  ↓
ToolMessage.content = top-K passages + source URLs
ToolMessage.artifact = full extracted text per URL (never enters the prompt)
```

Each stage degrades to the stage above it. A page that will not fetch, will not extract, or ranks
below threshold falls back to its DuckDuckGo snippet — so the tool is never _worse_ than today.

### 4.1 The nuance that decides the design

**Fetch-and-send-everything is a regression.** Four real pages are far larger than four snippets;
shipping them raw would increase token cost and bury the answer. The ranking stage is not a
refinement of this feature — **it is the feature**. The ordering (extract → rank → send top-K) is
what converts "more content" into "better answers at similar or lower cost".

This is the same mechanism Exa sells as `highlights` and Firecrawl as query-shaped extraction; the
difference is we run it locally and unmetered.

### 4.2 Where the artifact finally earns its place

Post-fix, `content` carries the ranked passages the model reasons over; `artifact` carries the full
extracted text per source. The artifact stops being a rename-duplicate (§1a) and becomes what the
channel is for: material retained for citation resolution, follow-up questions, and
"read more from source 2" without a re-fetch — none of which costs prompt tokens.

---

## 5. Phasing

Each phase is independently shippable and independently measurable.

Revised after the §8 decisions — there is no ranking phase.

| Phase  | Scope                                                                                                   | Ships value alone?                     |
| ------ | ------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| **S0** | The `response_format` fix (separate program) — stop paying twice for duplicate bytes                    | Yes — pure cost win, no quality change |
| **S1** | Fetch + extract + **snippet-anchored window**, in parallel, with per-fetch timeout and snippet fallback | Yes — this is the feature              |
| **S2** | Caching (per-workspace, on disk, TTL + LRU) and politeness (concurrency cap, robots, client identity)   | Yes — latency and good-citizenship     |

**S1 must hit AC2's token budget on its own.** With no later ranking stage to redeem it, the
window size _is_ the cost control: a window big enough to contain the answer and small enough that
four of them cost about what four snippets cost today. Tune it against AC4's measurements, and
treat a miss as a reason to shrink the window before adding machinery.

---

## 6. Acceptance criteria

Deliberately measurable. A phase is not done until its ACs are demonstrated **by measurement, not
by unit tests alone** — the MCP catalog shipped green on hermetic tests and failed on the real app,
and that mistake is not to be repeated.

**AC1 — never worse than today.** For a query where every fetch fails, the tool returns the same
four DuckDuckGo snippets it returns today. Failure of the new path is a fallback, never an error.

**AC2 — token budget honoured.** `content` for a single search never exceeds a configured token
budget (a hyperparameter, per the hyperparameters PRD). Asserted by measurement, not by hoping.

**AC3 — answer quality improves on a fixed question set.** A small held-out set of questions whose
answers exist below the fold of a page (i.e. _not_ in the snippet) is answered correctly more often
than the snippet-only baseline. Without this AC the feature is unfalsifiable.

**AC4 — cost is stated, not assumed.** Report measured tokens/search for: today, after S0, after S1,
after S2. If S2 does not land at or below the S0 number, the ranking is not doing its job.

**AC5 — no query leaves the machine beyond discovery.** Page fetches go directly to the origin. No
third-party extraction or ranking service.

**AC6 — bounded latency.** A search completes within a configured wall-clock budget; slow fetches
are abandoned and fall back to their snippet rather than stalling the turn.

---

## 7. Risks and honest costs

| Risk                                         | Mitigation                                                                                       |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------ |
| **Latency**: 4 sequential fetches is seconds | Parallel fetch, hard per-fetch timeout, AC6 budget                                               |
| **Bot blocking / paywalls / JS-only pages**  | Fall back to snippet (AC1); the browser capability covers JS at higher cost                      |
| **Extraction quality varies**                | `trafilatura` is good, not perfect; rank on extracted text so bad extraction ranks low naturally |
| **Fetching increases cost if S2 slips**      | Truncation budget in S1; do not ship S1 with a generous budget and no S2 date                    |
| **Politeness / being a bad citizen**         | Concurrency cap, timeouts, honour robots, identify the client                                    |
| **Local embeddings add a model dependency**  | Already present; degrade to lexical (BM25) ranking when no embedding model is configured         |

**A real risk worth naming:** this makes `web_search` meaningfully slower for a modest quality gain
on _easy_ questions, and a large gain on _hard_ ones. If most real usage is easy lookups, the
latency cost may not be worth it. AC3's question set is what settles that — build it first.

---

## 8. Decisions (settled)

1. **Extraction runs INSIDE `web_search`.** No separate `fetch_page` tool. A second tool means a
   second model round-trip to decide to call it — more latency and more tokens for the same bytes.
   Every search silently gets better; the agent's contract is unchanged.

2. **No embedding ranker. Snippet-anchored windowing instead.** This supersedes §4.1's claim that
   ranking "is the feature" — that was wrong, and the reasoning is worth keeping visible:

   DuckDuckGo has _already told us which part of the page matched_ — that is what the snippet is.
   So we do not need a model to find the relevant region; we need to locate the snippet inside the
   extracted article and take a window around it. That yields the passage the ranker would have
   selected, with **no embedding model, no extra dependency, and no added latency**.

   Fallback chain, cheapest-first:
   - snippet text located in the extracted article → return a window around it (the common case);
   - snippet not locatable (rewritten/truncated by the engine) → return the article's lead, capped;
   - extraction failed → return the DuckDuckGo snippet (AC1).

   **BM25 is deferred, not designed in.** Add it only if AC3/AC4 measurement shows windowing is
   insufficient, and then via an existing optimised library (`bm25s` or `rank_bm25`) — do not
   hand-roll a scorer. Local embeddings are explicitly out of scope.

3. **Cache: per-workspace, on disk, short TTL.** Chosen over per-run (re-fetches the same URL every
   turn — the common case is a follow-up question about the page just read) and per-conversation
   (does not survive a restart, which the desktop app does often). Keyed by URL, stored under the
   workspace's real directory, reusing the real-filesystem pattern the MCP catalog work establishes
   rather than inventing a second one. TTL short enough that news stays fresh (start at 24h, a
   hyperparameter). Bounded by size with LRU eviction — an unbounded on-disk cache on a user's
   laptop is a bug, not a feature.

4. **Desktop only.** Web/self-host is out of scope entirely — no gate, no fallback path, no
   dual-mode code. The fetch happens on the user's machine or it does not happen.

### What this removes from the plan

Dropping the ranker removes the embedding dependency, the BM25 floor, and the "S2 pulls cost back
down" argument. The phasing collapses: **S1 (fetch + extract + snippet-anchored window) is now the
whole feature**, and it must hit AC2's token budget on its own rather than relying on a later
ranking stage to redeem it. That is a simpler and more honest plan.

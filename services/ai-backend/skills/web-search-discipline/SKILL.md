---
name: web-search-discipline
description: Plan and tighten web_search calls — write high-yield queries, recognize diminishing returns, and stop when the next search will not add information. Load when planning a search batch or when consecutive searches stop helping.
allowed_tools:
  - web_search
---

# Web search discipline

The per-tool budget caps `web_search` at 5 invocations per task. Treat that cap as a planning constraint, not a retry budget — most questions finish in 1–3 well-formed queries.

## Before you call web_search

Spend a moment writing the queries you intend to issue. A good batch covers different facets of the question, not the same facet from slightly different angles. For each planned query, ask:

- Does it target a **distinct facet** (different entity, different attribute, different time period, different source) from the others in the batch?
- Does it have **enough specificity** to surface canonical sources rather than aggregator pages? Add the entity name in full, the year if relevant, and the publication or domain if you have one in mind.
- Could a `site:` filter (`site:cardekho.com`, `site:docs.python.org`, `site:github.com`) cut noise?

If two of your planned queries differ only in synonyms or word order, drop one — they will return overlapping hits.

## Recognizing diminishing returns

Stop calling `web_search` when any of these is true:

- The last result returned the same sources as the previous one, with no new figures or facts.
- You already have enough evidence to answer with appropriate uncertainty notes.
- You have issued 3 well-formed queries and the gaps that remain are matters of opinion or recommendation rather than fact.

When you stop, your final answer should:

1. Lead with the answer or the recommendation.
2. Cite the sources you used inline, with concise link labels.
3. Name explicitly what is still uncertain, rather than searching to close every loop.

## When you are stuck

If 2–3 well-formed queries did not surface what you need, switch tactics rather than rephrasing:

- Try a different source — `site:` a vendor, an authority, or a forum.
- Search for a related artifact (a PR, a docs page, a press release) that links to the answer.
- Ask the user a sharp clarifying question instead of burning more budget guessing.

## Anti-patterns

- **Paraphrase loops.** Issuing `"Honda Elevate engine specifications"`, then `"Honda Elevate 1.5 engine specs India"`, then `"Honda Elevate 1.5 i-VTEC specifications"` back-to-back. Pick one, get the result, move on.
- **Open-ended phrasing inflation.** A user asking "compare X and Y" does not require exhaustive coverage; pick the sharpest 2 queries — typically one per entity, or a single comparative query.
- **Reissuing on success.** If a query already gave you a usable answer, do not re-search to "double-check" — cite the source and continue.
- **Drift from the user's actual question.** Each query should still be in service of the original task. If your queries have drifted, restate the task to yourself before issuing the next one.

# PRD-06 — Word-level text diff + prose renderDiff upgrade (Wave 1)

**Goal:** the VSCode/Cursor-style red/green inline diff for *text-shaped* surfaces. A dependency-free word-level differ in chat-surface, consumed by the email tier-1 renderer and the message/doc archetypes, with hunk structure that PRD-09 will use for per-hunk accept.

**Depends on:** PRD-01 (and PRD-03's Message/Doc renderers if merged; otherwise land the util + email upgrade and leave archetype adoption as a listed follow-up). **Scope:** `packages/chat-surface` (util) + `packages/surface-renderers` (consumers).

## Scope — files

| File | Change |
|---|---|
| `packages/chat-surface/src/textdiff/wordDiff.ts` | NEW — pure module: `wordDiff(before: string, after: string): DiffHunk[]` where `DiffHunk = {id, kind: "equal"\|"insert"\|"delete", text}`. Tokenize on whitespace boundaries keeping separators; LCS via O(ND) Myers on the token arrays (~150 LOC); coalesce adjacent same-kind runs; hard cap: inputs >20k chars fall back to a single delete+insert pair (budget safety). Deterministic `id` = index-based (stable across renders for the same pair) |
| `packages/chat-surface/src/textdiff/DiffText.tsx` | NEW — presentational: renders hunks as `<del>`/`<ins>` semantic elements with palette-consistent styles (delete: struck-through, danger-tinted; insert: accent/ghost-tinted background). Props: `hunks`, optional `onHunkToggle(id)` (renders nothing interactive when absent — pure display now, PRD-09 activates it) |
| `packages/chat-surface/src/index.ts` | EXTEND — export `wordDiff`, `DiffHunk`, `DiffText` in a new delimited barrel block per the barrel discipline |
| `packages/surface-renderers/src/email/EmailRenderer.tsx` (and `MessageRenderer`/`DocRenderer` if present) | EXTEND — `renderDiff`: when the diff payload carries `{before_body, after_body}` strings, render `DiffText(wordDiff(before, after))` inside the existing PENDING block instead of the plain ghost paragraph; keep the streaming ghost treatment while `streaming` (diff computes once on `pending`) |

## Behavior (normative)

- Word-level, whitespace-preserving: `"Hi Jordan," → "Hi Maya,"` yields equal("Hi ") delete("Jordan,") insert("Maya,").
- Never rendered for non-prose archetypes (record/table diffs stay field/cell-level — that's the right grammar for them).
- Accessibility: `<ins>`/`<del>` semantics + `aria-label` totals ("3 insertions, 1 deletion") on the container; color is never the only signal (strikethrough/underline carry it).
- Performance: 5k-word before/after diffs in <10 ms (unit-benchmarked with a loose 50 ms CI bound); the 20k-char cap guards the 100 ms mount budget.

## Acceptance criteria

1. Golden diffs: 6 fixture pairs (single word swap; sentence insert; paragraph delete; trailing-whitespace-only change ⇒ equal-dominant; fully-different ⇒ delete+insert; identical ⇒ single equal).
2. Property test: for random token sequences, concat(equal+delete)==before and concat(equal+insert)==after.
3. Cap test: 25k-char inputs return exactly the 2-hunk fallback.
4. EmailRenderer diff snapshot: before/after body renders del/ins nodes; streaming state still shows the ghost block (no diff yet).
5. Typecheck + vitest + package eslint green in both packages; no new dependencies.

## Non-goals / guardrails

- No per-hunk accept/reject behavior (PRD-09 — but the `id`/`onHunkToggle` seam must exist).
- No line-based unified-diff view, no code syntax highlighting (tier-4 territory).
- No changes to structured diffs (Opportunity/Sheet/Generic) — different grammar, already correct.

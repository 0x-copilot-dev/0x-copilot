# Verification playbook — Generative Surfaces v2 PRDs

**Purpose.** The 14 PRDs in `prds/` were authored by parallel agents grounded in repo
facts packs, but (except one pass on B4) they have **not** been verified. This playbook is
the exact verification program to run — written to be pasted to Opus (or any strong
model), one prompt per pass. Run stages in order; stages 1–2 parallelize per PRD, stages
3–4 need all PRDs done first.

**Already done:** `PRD-B4` received its Stage-1 accuracy audit (2 corrections applied:
the `build_surface_spec_store` location — it lives in `backend_store.py`, not `store.py` —
and the chat-transport `Transport` port call shape). Everything else: pending.

**Ground rules for every verifier prompt below** (prepend to each):

```text
ROOT (absolute, work ONLY here):
/Users/parthpahwa/Documents/work/enterprise-search/.claude/worktrees/generative-ui-spec-authoring-efac2d
Canary: `test -f "$ROOT/docs/plan/generative-surfaces-v2/02-sdr.md" && echo OK` — if not OK, stop.
Docs task: never modify files outside docs/plan/generative-surfaces-v2/; reading code
anywhere under ROOT is expected. Never commit. Never weaken a Definition-of-Done item.
Authoritative contract: 02-sdr.md §5 (event vocabulary), ledger id format `r<short>·<seq>`,
runtime flag `SURFACES_V2`.
```

**The 14 files** (all under `docs/plan/generative-surfaces-v2/prds/`):
PRD-A1-ledger-contracts.md · PRD-A2-usage-meter.md · PRD-A3-ledger-emission-surface-store.md ·
PRD-B1-canvas-mount.md · PRD-B2-provenance-fallback.md · PRD-B3-view-lifecycle.md ·
PRD-B4-suggest-shape.md · PRD-C1-classifier-policy.md · PRD-C2-tool-access-gate.md ·
PRD-D1-staged-write-engine.md · PRD-D2-commit-engine.md · PRD-D3-bulk-rowset.md ·
PRD-E1-receipt-sources.md · PRD-E2-approvals-agents-tab.md · PRD-E3-audit-usage-retirement.md

---

## Stage 1 — Reference-accuracy audit (one run per PRD; parallelizable)

> Adversarial reference audit of `docs/plan/generative-surfaces-v2/prds/<FILE>`. For
> EVERY concrete claim about the existing repo — file paths, class/function names, flags,
> env vars, endpoints, commands, test paths — verify it against the code under ROOT.
> Distinguish NEW names the PRD defines (fine) from claims about existing code (must be
> true). Fix wrong references by editing the file directly when you can determine the
> correct value; otherwise rewrite the claim as an explicit `VERIFY AT IMPL:` note.
> Resolve existing `VERIFY AT IMPL:` markers where the repo answers them. Do not change
> scope, design, or DoD. Return: corrections made, markers resolved, markers remaining.

**Pass bar:** zero claims about existing code left unverified; every remaining
`VERIFY AT IMPL:` is genuinely undeterminable from the repo.

## Stage 2 — Implementability audit (one run per PRD; parallelizable)

> Implementability audit of `docs/plan/generative-surfaces-v2/prds/<FILE>`. Roleplay a
> fresh engineer (possibly a smaller model) whose ONLY inputs are this PRD plus
> 01-problem-and-requirements.md, 02-sdr.md, 03-prds.md. Walk the implementation plan
> step by step: at each step, could you act without guessing? Hunt for: interfaces
> referenced but never defined, steps with no file path, DoD items with no way to prove
> them, missing commands, ambiguous ordering, contradictions with the SDR. Where the
> answer is determinable from the SDR or repo, fix the PRD directly. Where it is a
> genuine open decision, add it to a `## Open questions` section at the bottom. Do not
> weaken any DoD item. Return verdict: READY / READY-WITH-QUESTIONS / GAPS (+ what you
> fixed, questions added).

**Pass bar:** every PRD is READY or READY-WITH-QUESTIONS; no GAPS remain.

## Stage 3 — Cross-PRD consistency sweep (single run, after stages 1–2)

> Read 02-sdr.md (§5 authoritative) and ALL 14 prds/\*.md. Enforce across the set, fixing
> files directly: (1) event names/fields match SDR §5 verbatim everywhere; (2) flag names
> identical — runtime `SURFACES_V2` plus ONE chat-surface canvas-flag name (align all
> PRDs to PRD-B1's choice); (3) endpoint paths identical between producer and consumer
> PRDs (A3's surfaces replay, B4's shape-request, D1/D2 decision endpoints, E3 usage);
> (4) ledger id format `r<short>·<seq>` everywhere; (5) every "Interfaces consumed" item
> is actually exposed by the PRD it names — fix the dependency or the claim; (6) no two
> PRDs create the same file with conflicting content plans; (7) additive payload fields
> introduced by later PRDs (e.g. D1's `proposal_ref`/`authorship_spans`) are reflected
> back into A1's contracts PRD as versioned additions, not contradictions. Then REWRITE
> 03-prds.md as an index: keep the standard DoD + UI DoD preamble verbatim, then a wave
> table linking each prds/ file with a one-line goal, a mermaid dependency graph, and a
> suggested implementation order. Return drift found+fixed per PRD, and anything you
> could not reconcile.

## Stage 4 — Coverage critic (single run, read-only)

> Read 01-problem-and-requirements.md and every prds/_.md. Map every FR-_ and NFR-\*
> (excluding the explicitly deferred: FR-E4 timeline, FR-G4 usage UI, Phase-2
> failure-path UX) to the PRD(s) implementing it. Return a coverage table plus: any
> requirement with NO covering PRD or partial coverage, and DoD-level blind spots across
> the set. Do not edit files — report only; uncovered items become new PRDs or scope
> amendments decided by the user.

## Acceptance bar for the whole program

- [ ] Stage 1 clean on all 14 (B4 may skip; include it in stages 3–4 regardless).
- [ ] Stage 2: 14× READY / READY-WITH-QUESTIONS; open questions aggregated for the user.
- [ ] Stage 3: zero unreconciled drift; 03-prds.md rebuilt as the index.
- [ ] Stage 4: coverage table shows every non-deferred FR/NFR covered, or the gap is
      explicitly accepted by the user.

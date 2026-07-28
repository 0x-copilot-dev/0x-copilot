# Staging/review audit — historical record

This file preserves the 2026-07-27 staging/review test-session history. It is
not the current Generative Surfaces backlog or release verdict.

The authoritative current status is:

- [`docs/plan/generative-surfaces-v2-1/README.md`](../../../docs/plan/generative-surfaces-v2-1/README.md)
  for PRD reconciliation, active architectural work, evidence gates, and stale
  closures;
- [`JOURNEYS.md`](./JOURNEYS.md) for the G0–G10 journey contract;
- `runs/` for git-ignored local execution artifacts.

## What this historical run established

- the corrected workspace mutation-boundary work was mergeable and its focused
  safety tests passed at the then-current branch;
- the installed supervised Desktop app reached sign-in, BYOK setup, and the
  native workspace chooser;
- G1/G2 did not complete in that session because the automation host lacked
  usable macOS Accessibility permission;
- the original v3 review-surface comparison found substantial visual and
  functional drift and directly motivated the shared review-surface
  architecture, persistent edit approval context, and partial-apply retry work.

These observations remain useful history. They are not current product
findings.

## Superseded results

The original 57-HIGH review result and its per-screen backlog are closed.
Current checked-in reports are:

- `tools/design-parity/surfaces/generative-surfaces-v3/out/report.md`:
  0 HIGH / 0 MEDIUM for the mapped draft, bulk, partial, and Sources states;
- `tools/design-parity/surfaces/artifact-dataset/out/report-v3-shared.md`:
  0 HIGH / 0 MEDIUM for the mapped artifact/dataset states.

The old Accessibility state is also not an enduring product defect. A future
release run must perform a fail-fast host preflight before BYOK/model use and
then produce a new revision-bound G1/G2 result.

## Historical reproduction commands

These commands describe the old audit surfaces; their output must not be
reported as a current release receipt without rerunning them at the target
revision.

```bash
node tools/design-parity/lib/run-chat-tool-call-shell-parity.mjs
node tools/design-parity/lib/run-generative-surfaces-v3-parity.mjs

G1_RELEASE=1 python3 tools/desktop-journeys/generative-workflows/g1_markdown_lifecycle.py
G2_RELEASE=1 python3 tools/desktop-journeys/generative-workflows/g2_csv_lifecycle.py
```

## Historical finding IDs

GSQA-001 through GSQA-008 and GSB-001 through GSB-013 are archived identifiers.
Do not reopen or extend those tables here. Route any still-valid behavior to
the deduplicated `GS-ARCH-*` or `GS-EVID-*` item in the current program ledger.

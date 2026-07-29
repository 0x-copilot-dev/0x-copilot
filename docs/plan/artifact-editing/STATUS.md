# Artifact editing — status

Source of truth for progress and Definition-of-Done checkoff.

**Baseline:** `main@4bb1ed34` (2026-07-29). Branch: `claude/gen-ui-surfaces-v2-6d4781`.

A box is ticked only when the behaviour is demonstrated by a test or a live
observation recorded here. A merged commit is not evidence.

## PRD-01 — user-edit causal lane ✅ (commit `02717d09`)

- [x] Editing a cell and saving succeeds on a completed run —
      `test_a_user_edit_succeeds_although_every_run_has_sealed` drives the live
      repro with **both** runs terminal and asserts revision 2 lands.
      _Live re-verification against the packaged app still pending._
- [x] "A newer revision exists" appears only when one does; the seal refusal is a
      distinct type and code — `test_a_sealed_run_is_not_reported_as_a_stale_revision`
      asserts `ArtifactErrorCode.SEALED_RUN` and non-`ArtifactConflictError`.
      Client reports a lost update only on `artifact_conflict`.
- [x] Model-authored writes to a sealed run remain refused, before any blob is
      written — `test_a_sealed_acting_run_is_refused_before_any_write`.
- [x] Lane is provably not client-controllable —
      `test_the_lane_follows_authorship_not_the_request` sends identical request
      fields and varies only server-held authorship;
      `test_a_user_edit_ignores_a_supplied_acting_run` proves a supplied run
      cannot drag a user edit back into a run ledger.
- [x] ai-backend `tests/unit` **6929 passed**, 97 skipped, 0 failed ·
      chat-surface **3302 passed**. The single chat-surface failure
      (`canvasLifecycle` differential) fails identically on an untouched main
      checkout — its runner needs a `PYTHONPATH` this environment does not set.

Design change made during implementation: the PRD had proposed an
`acting_conversation_id` on the wire for symmetry. Building it showed that to be
wrong — the artifact record already carries its conversation, so the field would
have added a forgeable input for a derivable fact. Dropped; PRD updated to match.

## PRD-02 — `artifact.revise` + tab identity ✅ (commit `9fa5b836`)

- [x] "Add one more row" produces revision 2 of the same artifact —
      `test_revision_appends_to_the_same_artifact` asserts one artifact id,
      `parent_revision: 1`, and that the fake fails loudly if publication is
      called instead. The tab follows because the projection already keys on
      `artifact_id`. _Live re-verification pending._
- [x] Tabs show the artifact's real title — the Run cockpit overlays the
      conversation-canvas record's title onto its own tabs, so both derivations
      agree. chat-surface suite green including the tab-label contract test.
- [x] Model cannot clobber a user revision — `parent_revision` is required and
      `test_a_lost_compare_and_append_does_not_write` asserts a lost CAS returns
      a failure carrying no `artifact_id` and no internal detail.
- [x] Conformance registers `("artifact", "revise")`; the descriptor catalog and
      E2 conformance report both accept it (`test_e2_final_conformance` green).
- [x] ai-backend **6938 passed** · chat-surface **3302 passed** · chat-surface
      typecheck clean.

Deviation from the PRD, recorded: D4 proposed making the tab title come from the
event. The `artifact.created` payload carries no title, and adding one means
changing a three-way SSOT (contract JSON + pydantic + api-types, pinned by
parity tests). The conversation-canvas record already holds the title
authoritatively, so that is used instead. D5's `kind` fallback landed as
specified.

## PRD-03 — revision review ⬜ NOT STARTED

- [ ] A model revision presents a diff with keep/revert rather than a silent swap
- [ ] Revert appends rather than rewrites; all revisions remain retrievable
- [ ] Execution mode is recorded and auditable on artifact operations
- [ ] No pre-commit gate was added to the artifact write path
- [ ] chat-surface / surface-renderers / ai-backend green

Nothing here is built. The primitives it wires (`compareArtifactText`,
`DiffText`, `restore`) already exist, so this is expected to be small, but it is
not done and must not be read as done.

## PRD-04 — truthful publication 🟡 PARTIAL (D1/D2 in `9fa5b836`)

- [x] Publish and revise results state destination explicitly —
      `stored_in="artifact_library"`, `wrote_to_filesystem=False`, both asserted.
- [x] Narration rule present on both tool descriptions, binding the model's
      claim to the result field rather than to prose it read once.
- [ ] Hermetic eval asserts no filesystem claim when workspace is disabled;
      baseline committed. **Not done** — this is the part that would catch a
      regression, so the defect is mitigated but not yet pinned.
- [x] ai-backend **6938 passed**.

## Live end-to-end verification ⬜ NOT DONE

Every box ticked above rests on unit/contract tests. The packaged app has **not**
been re-staged or driven against these changes.

`tools/desktop-journeys/README.md` is explicit that
`apps/desktop/resources/runtime/**` is a snapshot of the Python services, so a
journey run without re-staging would exercise the old backend. The relevant
journeys already exist —
`tools/desktop-journeys/generative-workflows/g2a_csv_artifact_surface.py` drives
a real CSV artifact surface, and the credentialed pass reads a key through
`load_env_key` from `services/ai-backend/.env`.

Required before this can be called verified:

1. re-stage (`make desktop-install` or `node tools/desktop-runtime/stage.mjs`);
2. run the G2/G2a journeys plus a scripted repro of both reported bugs;
3. record the run directory and outcomes here.

## Evidence log

Recorded as work lands. Live-repro evidence from the original diagnosis:

- `ai-backend.log`: 2 × `POST /v1/agent/artifacts/{id}/revisions` → **409**; the
  only two 409s in the log.
- `artifact_repository.jsonl`: `art_41618344-…` `current_revision: 1`,
  `parent_revision: null` — no newer revision existed when "A newer revision
  exists" was shown.
- `runs.jsonl`: run `4435d40de4834163a151e3ddc12dbeb4` `status = completed` at
  `2026-07-29T05:34:27.803802Z`.
- Two artifact IDs (`art_41618344-…` 229 B, `art_eb235acb-…` 269 B), both
  `revision: 1`, both `parent_revision: null` — the duplicate-tab cause.
- `RUNTIME_ENABLE_DESKTOP_WORKSPACE=false` on the live ai-backend process; no CSV
  in `~/Documents` — the publication claim was structurally impossible.

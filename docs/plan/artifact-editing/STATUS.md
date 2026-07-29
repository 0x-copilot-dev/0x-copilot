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

## PRD-02 — `artifact.revise` + tab identity

- [ ] "Add one more row" produces revision 2 of the same artifact and one tab
- [ ] Tabs show the artifact's real title
- [ ] Model cannot clobber a user revision (CAS enforced)
- [ ] Conformance asserts human/model surface parity for the artifact domain
- [ ] ai-backend / chat-surface green

## PRD-03 — revision review

- [ ] A model revision presents a diff with keep/revert rather than a silent swap
- [ ] Revert appends rather than rewrites; all revisions remain retrievable
- [ ] Execution mode is recorded and auditable on artifact operations
- [ ] No pre-commit gate was added to the artifact write path
- [ ] chat-surface / surface-renderers / ai-backend green

## PRD-04 — truthful publication

- [ ] Publish and revise results state destination explicitly
- [ ] Narration rule present on both tool descriptions
- [ ] Hermetic eval asserts no filesystem claim when workspace disabled; baseline committed
- [ ] ai-backend green

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

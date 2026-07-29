# PRD-02 — `artifact.revise` for the model, and canvas tab identity

**Status:** specified
**Closes:** live Bug 2 (a) and (b)
**Ledger impact:** GS-ARCH-06 (B1/A2 "draft-version promotion"); B3 row "stabilize tab identity/order"

## Implementer brief

Asked to "add 1 more row", the model minted a **second artifact** and a second
tab, because create is the only verb it has. Give it a revise verb over the
domain service that already exists, and make the canvas tab carry the artifact's
real name instead of a synthesized one.

## Context

### Observed failure (live, 2026-07-29)

Two artifact IDs for what the user considers one CSV plus a row:

| artifact              | bytes | digest         | revision | parent |
| --------------------- | ----- | -------------- | -------- | ------ |
| `art_41618344-be31-…` | 229   | `1ddf5f33b32c` | 1        | `null` |
| `art_eb235acb-9153-…` | 269   | `874af754954e` | 1        | `null` |

Both `revision: 1`, both `parent_revision: null`, both titled "Random Data CSV",
and the canvas showed two tabs labelled identically `dataset artifact · r1`.

### Root cause (a) — asymmetric exposure of a correct service

`ArtifactService.append_revision` exists, is CAS-checked, and is used today by the
human HTTP route. The model's toolset never got the verb:

- `PublishArtifactInput` (`publish_artifact.py:93`) has `kind`, `title`,
  `media_type`, `content`/`content_ref`, `suggested_filename`,
  `presentation_preference` — **no `artifact_id`, no `parent_revision`**.
- `PUBLISH_ARTIFACT_TOOL_DESCRIPTION` says _"**Create** one durable … artifact."_

One correct domain service, two consumers (human HTTP, model tools), and only one
got the full verb set. Nothing asserted the surfaces agree — the operation
inventory at `conformance.py:220` honestly records `("artifact", "publish", …)`
and no revise, because revise genuinely does not exist for the model.

This is not a boundary or SSOT fault. It is an incompleteness of exposure, and the
fix includes the assertion that stops it recurring.

### Root cause (b) — display identity derived twice

`artifactProjection.ts:58` synthesizes `title: \`${kind} artifact · r${revision}\``.
The authoritative `title` ("Random Data CSV") is on the artifact record and is
rendered in the panel header, but the tab discards it. Two distinct artifacts of
the same kind and revision therefore get byte-identical labels.

The tab **keying** is already correct: entries are keyed by `artifact_id` and
`ARTIFACT_REVISED` updates the entry in place. So once (a) is fixed, one tab
correctly bumps to r2 — no tab-machinery change is needed for that half.

## Interfaces consumed

- `ArtifactService.append_revision` — unchanged.
- `OperationGateway` — the single dispatch path for model-visible operations.
- PRD-01's causal lane — a model revise is `MODEL`-authored, therefore RUN lane.

## Interfaces exposed

```python
class ReviseArtifactInput(RuntimeContract):
    artifact_id: str                 # validated via ArtifactIdCodec
    parent_revision: PositiveInt     # compare-and-append; no blind overwrite
    content: str | None = None       # exactly one of content / content_ref
    content_ref: str | None = None
```

`kind` and `media_type` are **not** accepted — they are immutable properties of
the artifact and are read from the stored record. A revise cannot change what an
artifact _is_.

## Design

### D1. Revise routes through the existing gateway and service

`ReviseArtifactTool` mirrors `PublishArtifactTool`: parse untrusted model input →
build an `OperationRequest` → dispatch through `OperationGateway` → the same
`ArtifactService.append_revision` the human path uses, with `author=MODEL` and the
RUN lane from PRD-01. No second write path, no bypass of the operation gateway.

### D2. Compare-and-append is mandatory

`parent_revision` is required. A model that has stale content fails CAS rather
than silently clobbering a user's newer revision — which matters precisely because
PRD-01 now lets the user write revisions too. Failure returns the PRD-01
`REVISION_STALE` reason so the model can re-read and retry rather than guess.

### D3. Conformance asserts both surfaces agree

Register `("artifact", "revise", "agent_runtime.artifacts.service")` in the
conformance inventory, and add a test asserting every artifact-domain operation is
either reachable from both the human and model surfaces or explicitly annotated
human-only. This is the durable fix for the class of bug, not just the instance.

### D4. Tab identity comes from the record

`ArtifactSurfaceTab.title` becomes the artifact's `title`; `kind` and `revision`
are demoted to secondary display fields the renderer may compose. One projection,
sourced from the record, so the tab and the panel header cannot disagree.

Two artifacts sharing a title stay distinguishable because the tab is keyed by
`artifact_id`; disambiguation in the label is a presentation concern, not an
identity one.

### D5. Latent projection defect

`artifactProjection.ts` resolves an `ARTIFACT_REVISED` event's `kind` from
`prior?.kind` and drops the event when `undefined` — so a revision to an artifact
whose `ARTIFACT_CREATED` is not in the current event window never becomes a tab.
Observed by inspection, **not reproduced**. PRD-01 makes user revisions common, so
this is in scope to fix defensively: fall back to the artifact record's `kind`.

## Implementation plan

1. `capabilities/tools/builtin/revise_artifact.py` — `ReviseArtifactInput`,
   `ReviseArtifactTool`, adapter, mirroring `publish_artifact.py`.
2. `prompts/tools.py` — `REVISE_ARTIFACT_TOOL_DESCRIPTION`; amend the publish
   description to say publish is for **new** artifacts and revise for existing ones.
3. `capabilities/operations/conformance.py` — register `artifact.revise`.
4. `runtime_worker/handlers/run.py` + `approval.py` — compose the tool where
   `publish_artifact_tool` is composed today.
5. `execution/factory.py` — expose in `_model_visible_tools`.
6. `artifactProjection.ts` — title from record; `kind` fallback for D5.

## Test plan

- Model revises an existing artifact → revision 2, `parent_revision: 1`, **one**
  artifact ID, **one** tab. (Direct regression test for the live repro.)
- Revise with a stale `parent_revision` → refused `REVISE_STALE`, no write.
- Revise cannot change `kind` or `media_type` (fields rejected).
- Revise on an unknown/foreign `artifact_id` → not found, no cross-tenant read.
- Revise is dispatched through `OperationGateway` (no direct service call).
- Conformance test: no artifact-domain operation is human-only-by-accident.
- Projection: two artifacts → two distinguishable labels; revise → one tab, r2.
- Projection: `ARTIFACT_REVISED` without a preceding `ARTIFACT_CREATED` in-window
  still yields a tab.

## Definition of done

- [ ] "Add one more row" produces revision 2 of the same artifact and one tab.
- [ ] Tabs show the artifact's real title.
- [ ] Model cannot clobber a user revision (CAS enforced).
- [ ] Conformance asserts human/model surface parity for the artifact domain.
- [ ] `ai-backend`, `chat-surface` suites green.

## Out of scope

- Diff/approve UI (PRD-03).
- Renaming an artifact, or changing kind/media type after creation.
- Bulk/row-level revise semantics — this is whole-content compare-and-append.

## Guardrails

- Do **not** add a second write path; revise goes through the gateway + service.
- Do **not** make `parent_revision` optional.
- Do **not** let revise mutate immutable artifact properties.

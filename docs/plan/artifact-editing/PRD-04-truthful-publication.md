# PRD-04 — Truthful publication reporting

**Status:** specified
**Closes:** the grounding defect observed alongside Bugs 1 and 2

## Implementer brief

The model told the user the CSV was "saved to your documents folder". It was not,
and it could not have been. Make the tool result state where content actually
went, and constrain narration to that fact.

## Context

### Observed failure (live, 2026-07-29)

In both screenshots the model asserted a filesystem write:

> "This is the random data CSV … that was saved to your documents folder."
> "The CSV is also saved as an artifact in your documents folder."

All three of the following are true:

- `PUBLISH_ARTIFACT_TOOL_DESCRIPTION` states publication _"does not save a file to
  the user's local workspace"_ (`prompts/tools.py:67`).
- The live process runs with `RUNTIME_ENABLE_DESKTOP_WORKSPACE=false` — it has **no
  filesystem capability at all**.
- `~/Documents` contains no such file.

The claim was not merely wrong, it was structurally impossible.

### Root cause

The publish result (`publish_artifact.py:207`) is:

```python
{"status": "created", "artifact_id": …, "revision": 1,
 "kind": …, "title": …, "presentation": …}
```

**It says nothing about destination.** The only statement of where content goes
lives in the tool _description_ — prose the model read once, competing against a
strong prior that "saving a CSV" means a file on disk. Given a result that is
silent on destination, the model filled the gap with the plausible thing.

This is a grounding failure, not a prompt-wording failure. The fix is to make the
authoritative fact travel _with the result_, where narration is actually formed.

This defect matters out of proportion to its size: the product's whole claim is
that you can trust the report of what it did. A confident false statement about a
side effect is the failure mode that most directly destroys that.

## Design

### D1. The result states its destination

```python
{"status": "created", …,
 "stored_in": "artifact_library",
 "wrote_to_filesystem": False}
```

`stored_in` is a closed enum, server-derived. `wrote_to_filesystem` is explicit
rather than implied by omission — a silent field is exactly what produced the
confabulation, so the negative is stated positively.

### D2. Narration is constrained to the result

The publish tool description gains an explicit narration rule: report the
destination **from the tool result**, and never claim a filesystem location unless
a filesystem operation actually returned one. The description already contains the
correct fact; what it lacked was an instruction binding narration to the result.

### D3. Capability-honest phrasing

When `RUNTIME_ENABLE_DESKTOP_WORKSPACE=false` no workspace tool is composed, so a
filesystem claim is impossible by construction. The system prompt should not leave
"saved to your documents" as an available inference in that configuration.

### D4. An eval pins it

Add fixtures to the existing PRD-11 hermetic eval harness (replay model, no live
provider) asserting that a publish-then-summarize turn produces **no filesystem
claim** when workspace is disabled. Regex-style assertion over the final response
for filesystem language ("documents folder", "saved to disk", "on your computer",
a path-like token). Cheap, deterministic, and it fails loudly on regression.

## Implementation plan

1. `publish_artifact.py` — add `stored_in` + `wrote_to_filesystem` to the result;
   same for the revise result from PRD-02.
2. `prompts/tools.py` — narration rule on both descriptions.
3. Eval fixtures + baseline update in the PRD-11 harness.

## Test plan

- Publish result carries `stored_in="artifact_library"`, `wrote_to_filesystem=False`.
- The fields are server-derived; model input cannot set them.
- Eval: publish-then-summarize with workspace disabled → no filesystem claim.
- Eval (adversarial): a user prompt asking "save it to my documents" still yields
  an honest answer about where it went, rather than an accommodating false one.

## Definition of done

- [ ] Publish and revise results state destination explicitly.
- [ ] Narration rule present on both tool descriptions.
- [ ] Hermetic eval asserts no filesystem claim when workspace is disabled;
      baseline committed.
- [ ] `ai-backend` suite green.

## Out of scope

- Implementing an actual "save to workspace" path (that is C2/C3 workspace work,
  and it is gated behind `RUNTIME_ENABLE_DESKTOP_WORKSPACE` for good reasons).
- General model-honesty work beyond artifact publication.

## Guardrails

- Do **not** fix this only in the prompt — the result must carry the fact.
- Do **not** let model input set destination fields.
- Do **not** weaken the workspace capability gate to make the claim true.

# Implementation PR Checklist

Use this checklist before opening any future implementation PR for the AI backend.

## Required Reading

- `docs/architecture/system-overview.md`.
- `docs/architecture/data-flow.md`.
- The matching technical spec in `docs/specs/`.
- `docs/testing/unit-testing-strategy.md`.
- `docs/testing/edge-case-matrix.md`.
- Relevant rule docs in `docs/rules/`.

## Required Evidence

Every implementation PR must state:

- Which architecture/spec behavior it implements or changes.
- Which Pydantic contracts were added or changed.
- Which edge cases from the matrix are covered.
- Which unit tests prove permission, validation, and failure behavior.
- Which external services are faked and where the fake boundary lives.

## Minimum Test Proof

Attach or summarize test output showing:

- Contract validation tests pass.
- Core success path tests pass.
- Permission denial tests pass.
- Malformed input tests pass.
- External failure tests pass.
- Regression tests pass for any fixed bug.

## Blockers

Do not merge implementation code if:

- A feature lacks unit tests.
- A model output can trigger action without Pydantic validation.
- Unauthorized capabilities can appear in model-visible context.
- Tests require real credentials or live services.
- A connector SDK object leaks into a runtime/domain contract.
- Subagents receive full conversation history by default.


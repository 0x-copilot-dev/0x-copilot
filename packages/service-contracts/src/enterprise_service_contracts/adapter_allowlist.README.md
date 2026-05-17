# Tier-2 Adapter Allowlist — Single Source of Truth

`adapter_allowlist.json` is the canonical specification of what tier-2
adapter source is allowed to import and reference. Both runtimes load it at
module-import time:

- **Desktop / 6A** — `apps/desktop/main/adapters/ast-allowlist.ts` derives
  `ALLOWED_IMPORTS` and `FORBIDDEN_GLOBALS` from this file (via
  `@enterprise-search/api-types`'s `ADAPTER_ALLOWLIST`).
- **AI backend / 6B** — `services/ai-backend/src/agent_runtime/capabilities/render_adapter_generator/capability.py`
  derives `_ForbiddenPattern.TOKENS` and `_ImportAllowlist.ALLOWED` from this
  file (via `enterprise_service_contracts.adapter_allowlist.load_adapter_allowlist`).

If the two runtimes disagree on what is allowed, agent-generated code can be
admitted by one side and rejected by the other — a confusing failure mode
and a security risk. Keeping the rules in one place removes that drift.

## Adding a new forbidden global

1. Edit `adapter_allowlist.json` — add the identifier to `forbidden_globals`.
2. Update the canary tests on each side so the snapshot still passes:
   - `packages/api-types/src/adapterAllowlist.test.ts`
   - `services/ai-backend/tests/unit/agent_runtime/capabilities/render_adapter_generator/test_adapter_allowlist_loader.py`
3. Open one PR. Both runtimes pick up the change at next module import — no
   other code change required.

## Adding a new allowed module / specifier

Same workflow: edit `allowed_imports`, update canaries, open one PR.

## Schema

```jsonc
{
  "schema_version": 1, // bump when adding a new top-level field
  "allowed_imports": {
    "<module-specifier>": ["<named-export>", "..."],
  },
  "forbidden_globals": ["<identifier>", "..."],
  "forbidden_syntax": ["eval", "Function", "__proto__"],
  "budget_ms": 100,
}
```

`forbidden_syntax` entries are handled by syntax-aware checks on the TS side
(`CallExpression`, `NewExpression`, `MemberExpression`) and by the Python
identifier regex check. Adding or removing entries here is more invasive than
adding to `forbidden_globals`; review carefully.

## Review bar

Changing this file is a security-sensitive operation. It governs what
arbitrary agent-generated code is allowed to do inside the tier-2 sandbox.
Treat changes the same way you treat changes to the AST scanner or the
adapter allowlist auditor — same reviewers, same scrutiny.

## What is NOT here

- The AST traversal logic (lives in `ast-allowlist.ts`).
- The codegen template logic (lives in `render_adapter_generator/templates.py`).
- The sandbox runtime (lives in `apps/desktop/main/adapters/sandbox.ts`).
- The lifecycle / registry host (lives in `apps/desktop/main/adapters/`).

This file is constants only.

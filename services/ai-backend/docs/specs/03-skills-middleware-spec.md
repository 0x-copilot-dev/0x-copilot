# Spec: Skills Middleware

## Purpose

Use Deep Agents skills and Agent Skills-compatible `SKILL.md` bundles for large task-specific instructions. Custom code should support discovery, validation, source precedence, and policy, while Deep Agents performs progressive disclosure.

## Architecture

Implemented modules:

- `capabilities/skills/sources.py`: configured skill source paths.
- `capabilities/skills/manifest.py`: optional product-side manifest parsing.
- `capabilities/skills/policy.py`: access policy for main agent and subagents.
- `capabilities/skills/middleware.py`: skill loading tool for model-selected
  skill markdown.
- `capabilities/skills/virtual.py`: backend-backed virtual skill cards and
  bundles for user-created Markdown skills.
- `agent_runtime/execution/factory.py`: resolves authorized skill cards and
  directories.
- `agent_runtime/execution/deep_agent_builder.py`: passes skill directories to
  `create_deep_agent`.

Do not invent a custom skill runtime. `SKILL.md`-compatible Markdown remains the
source of truth, whether it is read from a filesystem bundle or loaded through a
virtual backend-backed provider.

## Pydantic Contracts

Required product-side models:

- `SkillManifest`: `name`, `description`, `license`, `compatibility`, `allowed_tools`, `metadata`.
- `SkillSource`: path, precedence, scope, writable flag.
- `SkillAccessPolicy`: agent type, allowed sources, denied skill names, allowed tools.
- `VirtualSkillCard`: compact model-visible skill metadata from backend-owned
  skill registry state.
- `VirtualSkillBundle`: full Markdown and metadata loaded after the model asks
  for a skill by stable name.

Deep Agents should still read the actual skill files from configured backends.

## Virtual Skills

Virtual skills represent user-created or org-created Markdown skills stored by
`services/backend` rather than present on disk as `SKILL.md` files. The runtime
lists authorized cards through backend internal APIs and loads full Markdown only
when a skill is selected.

Design rules:

- Virtual cards must be scoped by `org_id` and `user_id` from the runtime
  context.
- Disabled skills are filtered before the model sees them.
- Duplicate enabled skill names are configuration errors.
- Provider failures become retryable capability load errors unless the provider
  already raised a typed runtime error.
- The runtime caches loaded bundles by skill name for a run-scoped registry.
- Virtual paths are identifiers for diagnostics and policy; they are not local
  filesystem paths and must not be opened directly.

Backend-backed providers should call `services/backend` internal skill routes,
not app-facing facade routes, because skill bundles are service-to-service
runtime material.

## Design Rules

- Skills are procedural memory; always-relevant context belongs in `AGENTS.md` memory.
- Descriptions must be precise because the model matches skills from frontmatter.
- Source precedence is numeric and deterministic: higher `precedence` wins for duplicate skill names, while Deep Agents receives source directories in ascending precedence order because later sources override earlier ones.
- Custom subagents do not inherit main-agent skills unless configured.
- Skill scripts require sandbox design before execution.

## Unit Tests

- Parse valid frontmatter and reject malformed manifests.
- Enforce source precedence with duplicate names.
- Reject path traversal in supporting asset references.
- Verify custom subagent skill isolation.
- Enforce description length and required fields.
- Load virtual skill markdown by name and cache repeated loads.
- Reject duplicate virtual skill names.
- Convert malformed virtual skill cards into typed configuration errors.

## Edge Cases

- Empty `SKILL.md`.
- Duplicate skill name with different source precedence.
- Missing referenced asset.
- Unsupported allowed tool name.
- Skill source exists but is not readable.
- Backend skill provider is unavailable.
- Backend returns a malformed virtual skill card or bundle.
- Virtual skill path resembles a filesystem path but must not be opened.

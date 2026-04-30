# Spec: Skills Middleware

## Purpose

Use Deep Agents skills and Agent Skills-compatible `SKILL.md` bundles for large task-specific instructions. Custom code should support discovery, validation, source precedence, and policy, while Deep Agents performs progressive disclosure.

## Architecture

Implemented modules:

- `skills/sources.py`: configured skill source paths.
- `skills/manifest.py`: optional product-side manifest parsing.
- `skills/policy.py`: access policy for main agent and subagents.
- `agent/factory.py`: passes skill directories to `create_deep_agent`.

Do not invent a custom skill runtime. `SKILL.md` is the source of truth.

## Pydantic Contracts

Required product-side models:

- `SkillManifest`: `name`, `description`, `license`, `compatibility`, `allowed_tools`, `metadata`.
- `SkillSource`: path, precedence, scope, writable flag.
- `SkillAccessPolicy`: agent type, allowed sources, denied skill names, allowed tools.

Deep Agents should still read the actual skill files from configured backends.

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

## Edge Cases

- Empty `SKILL.md`.
- Duplicate skill name with different source precedence.
- Missing referenced asset.
- Unsupported allowed tool name.
- Skill source exists but is not readable.


# PRD: Skills Middleware

## Problem

Some capabilities need more context than a tool description can carry: workflows, domain policies, examples, templates, and scripts. Always injecting that content bloats prompts and makes unrelated tasks worse.

## Goal

Use Deep Agents skills as Agent Skills-compatible `SKILL.md` bundles with progressive disclosure. The model sees frontmatter first and reads full instructions only when relevant.

## User Value

- Specialized workflows become available without increasing every prompt.
- Admins and developers can package reusable domain knowledge cleanly.
- Subagents can receive focused skill sets for their role.

## Scope

- Local skills directory conventions.
- Skill frontmatter requirements.
- Skill source precedence.
- Skill access policy for main agents and subagents.
- Guidance for skill scripts and sandbox execution later.

## Non-Goals

- A custom skill format that diverges from Agent Skills.
- Automatic loading of every skill file into system prompts.
- Executing skill scripts without an explicit sandbox design.

## Acceptance Criteria

- Each skill has `SKILL.md` frontmatter with stable name and clear description.
- Skill descriptions are concise and action-oriented.
- Full skill content is loaded only when matched.
- Specs define tests for manifest parsing, source precedence, and invalid skills.

## Edge Cases

- Empty or malformed frontmatter.
- Duplicate skill name across sources.
- Skill file larger than allowed limits.
- Supporting asset referenced in `SKILL.md` is missing.
- Subagent tries to access a skill outside its configured sources.

## Unit Testing Requirements

- Parse valid and invalid skill manifests.
- Enforce source precedence deterministically.
- Reject unsafe asset paths.
- Verify custom subagents do not inherit skills unless configured.


# Skills

How user-defined and preloaded skills are stored, versioned, and served to ai-backend.

See also:

- [architecture/02-contracts.md](../architecture/02-contracts.md) — `SkillRecord`, `InternalSkillBundle`
- [reference/internal-api.md](../reference/internal-api.md) — `/internal/v1/skills/*`

---

## What it does

The skill registry stores markdown-based system-prompt fragments that the agent loads
at run-start. A skill is a named, versioned markdown document with scope (`user` or `org`),
optional tool restrictions (`allowed_tools`), and a `virtual_path` for logical lookup.

Three source types coexist in the same list endpoint:

- `user` — created by the user via the Settings UI
- `preloaded` — seeded by the operator (org-scoped markdown files injected at deploy time)
- `system` — built into the ai-backend filesystem; served from `GET /internal/v1/skills/system` on ai-backend (never stored in backend)

---

## Key modules

| File                                     | Role                                                       |
| ---------------------------------------- | ---------------------------------------------------------- |
| `backend_app/service.py`                 | Skill CRUD orchestration                                   |
| `backend_app/store.py`                   | `SkillStore` — persistence                                 |
| `backend_app/contracts.py`               | `SkillRecord`, `CreateSkillRequest`, `InternalSkillBundle` |
| `backend_app/routes/skills.py` (implied) | HTTP route handlers                                        |

---

## CRUD operations

### Create (`POST /v1/skills`)

1. Parses `CreateSkillRequest(org_id, user_id, markdown, display_name, scope)`.
2. Extracts `name` from the markdown front-matter (`# Title` → slug) or from `display_name`.
3. Parses `SkillManifestFields` from the markdown (name, description, allowed_tools, compatibility).
4. Creates `SkillRecord` with `source_type=user`, `version=1`, `enabled=True`.
5. Stores via `SkillStore.upsert()`.
6. Returns `SkillResponse`.

### Update (`PUT /v1/skills/{skill_id}`)

1. Loads the existing record; confirms ownership (`org_id` + `user_id`).
2. Applies `UpdateSkillRequest` fields (markdown, display_name, enabled, scope).
3. Re-parses manifest fields if markdown changed.
4. Increments `version`.
5. Stores updated record.

### Delete (`DELETE /v1/skills/{skill_id}`)

Hard delete. Appends a skill audit event (`action=deleted`).

### List (`GET /v1/skills`)

Note: the facade's `GET /v1/skills` aggregates by calling both:

- Backend `GET /v1/skills` → user + preloaded skills
- ai-backend `GET /internal/v1/skills/system` → system skills

Then merges the lists (system skills first). The backend endpoint returns only skills
owned by `(org_id, user_id)`.

---

## Internal API (consumed by ai-backend)

| Route                                | What it returns                                                                            |
| ------------------------------------ | ------------------------------------------------------------------------------------------ |
| `GET /internal/v1/skills`            | `InternalSkillListResponse` — `InternalSkillCard[]` (no markdown) filtered by org and user |
| `GET /internal/v1/skills/{skill_id}` | `InternalSkillBundle` — full markdown + metadata for prompt injection                      |

`InternalSkillCard` omits `markdown` to keep the listing payload small. `InternalSkillBundle`
includes `markdown` and `metadata` for prompt injection at run time.

---

## Skill manifest parsing

`SkillManifestFields` parsed from the markdown document:

```markdown
---
name: my_skill
description: A brief description
license: MIT
compatibility: ["gpt-4o", "claude-3"]
allowed_tools: ["ask_a_question"]
---

# My Skill

System prompt content here...
```

If the YAML front-matter is absent, `name` falls back to the slug of the `display_name`
supplied in the request. `description` defaults to the first non-empty paragraph.

---

## Versioning

Each update increments `version` (integer, `≥ 1`). ai-backend receives the current version
when fetching the bundle. There is no version history — only the latest version is stored.
Version numbers are for cache-busting / staleness detection, not rollback.

---

## Audit logging

Skill events (`SkillAuditEventRecord`) are appended to the skill audit chain on:

- Create
- Update (content or metadata change)
- Enable / disable
- Delete

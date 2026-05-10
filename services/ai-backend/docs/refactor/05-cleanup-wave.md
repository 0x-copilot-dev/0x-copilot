# Refactor PRD — Cleanup Wave (Phase 2)

**Status:** Draft (scope reduced after pre-flight verification — see [§1.5](#15-pre-flight-findings-three-sub-items-withdrawn))
**Author:** architecture audit, May 2026
**Tracks:** [refactor-audit §5.6](../architecture/refactor-audit.md#56-6-empty-legacy-directories-under-agent_runtime)
**Roadmap:** [00-roadmap.md](00-roadmap.md) → P6
**Originally tracked also:** [refactor-audit §1.7](../architecture/refactor-audit.md#17-custom-migration-runner), [§1.8](../architecture/refactor-audit.md#18-encryptexistingcolumns-running-as-a-perpetual-job), [§5.7](../architecture/refactor-audit.md#57-dev_auth_bypass_allowed-toggle-on-deploymentprofile) — withdrawn after code reads invalidated the audit hypotheses (see [§1.5](#15-pre-flight-findings-three-sub-items-withdrawn))

---

## 1. Problem

One hygiene item: six legacy empty directories under `agent_runtime/` that confuse new readers and shadow live modules in tooling.

### 1.1 Six legacy empty directories under `agent_runtime/`

The architecture index Appendix A documents six top-level directories that contain only `__pycache__` (no `.py` files). They are remnants from before `agent_runtime/` was reorganized into `capabilities/`, `delegation/`, `context/memory/`, `execution/`. Verified empty by:

```bash
find services/ai-backend/src/agent_runtime/{agent,mcp,memory,skills,subagents,tools} -name "*.py"
```

returning no hits. Verified there are zero live imports:

```bash
grep -rEn "from agent_runtime\.(agent|mcp|memory|skills|subagents|tools)( |\.|$)" \
  services/ai-backend/src services/ai-backend/tests
# returns nothing
```

Live equivalents:

| Empty directory            | Live equivalent                                                                             |
| -------------------------- | ------------------------------------------------------------------------------------------- |
| `agent_runtime/agent/`     | `agent_runtime/execution/` (graph + builder) and `agent_runtime/capabilities/` (middleware) |
| `agent_runtime/mcp/`       | `agent_runtime/capabilities/mcp/`                                                           |
| `agent_runtime/memory/`    | `agent_runtime/context/memory/`                                                             |
| `agent_runtime/skills/`    | `agent_runtime/capabilities/skills/`                                                        |
| `agent_runtime/subagents/` | `agent_runtime/delegation/subagents/`                                                       |
| `agent_runtime/tools/`     | `agent_runtime/capabilities/tools/`                                                         |

**Why it's a problem:** search hits land in legacy paths first; new contributors get confused. Empty `__init__.py`-less directories are silently importable as namespace packages, which can mask broken imports in test runs.

### 1.5 Pre-flight findings: three sub-items withdrawn

The original PRD bundled four hygiene items. Pre-flight code reads (May 2026) invalidated the audit hypothesis on three of them. They are documented here so future readers don't re-walk the same ground.

#### Withdrawn — `dev_auth_bypass_allowed` is an active safety gate, not a stale toggle

The audit at [refactor-audit §5.7](../architecture/refactor-audit.md#57-dev_auth_bypass_allowed-toggle-on-deploymentprofile) hypothesized this field was stale because the root [`CLAUDE.md`](../../../../CLAUDE.md) says "DEV_AUTH_BYPASS no longer exists." The field is in fact a **fail-closed guard** in [`profile.py:147-160`](../../src/agent_runtime/deployment/profile.py#L147-L160):

```python
@classmethod
def _enforce_consistency(cls, profile_name, env, toggles):
    bypass_set = env.get("DEV_AUTH_BYPASS", "").strip().lower() == "true"
    if bypass_set and not toggles.dev_auth_bypass_allowed:
        raise DeploymentProfileError(
            f"DEV_AUTH_BYPASS=true is not allowed under "
            f"{ENV_DEPLOYMENT_PROFILE}={profile_name!r}; remove either the "
            f"profile or the bypass env var."
        )
```

Removing the toggle removes the consistency check. Even though the bypass _route_ no longer exists, the env-var guard still prevents a misconfiguration from silently doing nothing instead of fail-closing. Keep as-is.

#### Withdrawn — `migrate.py` is a thin wrapper over yoyo-migrations, not bespoke code

The audit at [refactor-audit §1.7](../architecture/refactor-audit.md#17-custom-migration-runner) hypothesized this was a custom migration runner. It is in fact a ~140-LOC wrapper around the **yoyo-migrations** library that adds operational guardrails:

- SQL-first authoring under [`migrations/`](../../migrations/) with explicit `NNNN_<topic>.rollback.sql` siblings (26 migrations as of this PRD).
- `MANIFEST.lock` checksums verified by `tools/check_migration_manifest.py` so silent edits to migration files are caught in CI.
- Distributed locking via `yoyo.backend.lock()` so concurrent applies are safe.
- `RUNTIME_MIGRATIONS_AUTO_APPLY` env gate (default true for dev/test, false for production deploys).
- Operational logging on every apply / rollback.

Switching to Alembic would lose SQL-first authoring, the explicit rollback files, and the manifest checksum guard — and gain nothing concrete because there's no SQLAlchemy ORM in use to benefit from autogenerate. **Keep as-is.**

#### Withdrawn — `EncryptExistingColumns` is a rate-limited resumable backfill, not a calcified one-shot

The audit at [refactor-audit §1.8](../architecture/refactor-audit.md#18-encryptexistingcolumns-running-as-a-perpetual-job) hypothesized this was a one-shot migration that calcified into a daemon. It is in fact a deliberately ongoing operational pattern (`FieldEncryptionBackfill`):

- Targets multiple `(table, column)` pairs; the doc-comment explicitly notes more columns will be added in C7 phase 2 once their writers land. Running as a loop means new columns get backfilled automatically when their writers ship.
- Idempotent on `encryption_version=0` — re-running advances the cursor without rewriting v1 rows.
- Rate-limited per `RUNTIME_ENCRYPTION_BACKFILL_BATCH` + `RUNTIME_ENCRYPTION_BACKFILL_SLEEP_MS`.
- Multi-worker safe via `FOR UPDATE SKIP LOCKED`.
- Pauses on KMS unavailability (`EncryptionUnavailableError`) and resumes on next pass — important for envelope-encryption KMS rotation.

Converting to a one-shot Alembic migration would lose all of those properties (resume, multi-worker, KMS-aware, ongoing column additions). **Keep as-is.**

### What this is NOT

- Not a behavior change anywhere. The 6 directories have no `.py` files and no imports.
- Not a switch of database, ORM, migration tool, encryption strategy, or auth model.

---

## 2. Goal and non-goals

### Goal

Delete six empty legacy directories from `agent_runtime/` so search results, IDE autocomplete, and namespace-package resolution all stop suggesting them.

### Non-goals

- Touch `dev_auth_bypass_allowed`, `migrate.py`, or `EncryptExistingColumns` — see [§1.5](#15-pre-flight-findings-three-sub-items-withdrawn) for why.
- Reorganize the live `agent_runtime/` subtree.
- Add or remove any `__init__.py` from live packages.

### Acceptance criteria

- All 6 directories removed:
  ```bash
  find services/ai-backend/src/agent_runtime/{agent,mcp,memory,skills,subagents,tools} -type d
  # returns nothing
  ```
- Pre-existing grep result still holds:
  ```bash
  grep -rEn "from agent_runtime\.(agent|mcp|memory|skills|subagents|tools)( |\.|$)" \
    services/ai-backend/src services/ai-backend/tests
  # returns nothing
  ```
- `__pycache__` artifacts cleaned (`.gitignore` already covers them; ensure no committed `.pyc` files remain after the deletion commit).
- Full test suite green (`make test`, plus per-service `pytest`).
- No new public API surface; no contract changes; no migration that requires downtime.

---

## 3. Systems touched

### 3.1 Empty-directory deletion (mechanical)

```bash
git rm -r services/ai-backend/src/agent_runtime/{agent,mcp,memory,skills,subagents,tools}
```

Pre-deletion verification (re-run before commit):

```bash
# Should return nothing
find services/ai-backend/src/agent_runtime/{agent,mcp,memory,skills,subagents,tools} -name "*.py" 2>/dev/null

# Should return nothing — confirms no live import resolves to these paths
grep -rEn "from agent_runtime\.(agent|mcp|memory|skills|subagents|tools)( |\.|$)" \
  services/ai-backend/src services/ai-backend/tests
```

If either returns a match, _do not delete_ — investigate first.

### 3.2 Documentation update

- Update [`docs/architecture/index.md`](../architecture/index.md) Appendix A: change "Verified empty by `find …` returning no hits" to past tense / "deleted in PR #N (P6)."
- Update [`docs/architecture/refactor-audit.md`](../architecture/refactor-audit.md) §5.6, §5.7, §1.7, §1.8 to reflect outcomes (one shipped, three withdrawn). Optional in this PR — can land separately.

---

## 4. Behaviors to preserve

| Behavior                                    | How preserved                                                             |
| ------------------------------------------- | ------------------------------------------------------------------------- |
| Every live import in `services/ai-backend/` | Pre-flight grep proves none target the empty directories                  |
| Encryption transform on existing rows       | Untouched — see [§1.5](#15-pre-flight-findings-three-sub-items-withdrawn) |
| Schema migration history                    | Untouched                                                                 |
| `DEV_AUTH_BYPASS` env-var fail-closed guard | Untouched                                                                 |
| Deployment profile resolution at startup    | Untouched                                                                 |

---

## 5. Risks

| Risk                                                                               | Likelihood | Mitigation                                                                                                                                 |
| ---------------------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| An empty-dir delete reveals a hidden namespace-package import we missed in grep    | Low        | Pre-delete grep covers `from`, `import`, and trailing-character forms; CI catches anything missed                                          |
| A reviewer expects the original four-item PRD and is confused by the reduced scope | Low        | Reduced scope is documented in [§1.5](#15-pre-flight-findings-three-sub-items-withdrawn) at the top of this PRD                            |
| A future search in IDE re-creates a legacy directory accidentally                  | Trivial    | The deletion sticks because there's nothing to import; if a developer creates a new `agent_runtime/agent/` it will conflict in code review |

---

## 6. Unit testing requirements

This is a no-behavior-change deletion. Tests should not change.

### 6.1 No-regression assertion

- `make test` and per-service `pytest` pass with zero test modifications.

### 6.2 (Optional) negative grep CI step

Add a CI step that asserts no first-party file imports from the deleted paths:

```bash
! grep -rEn "from agent_runtime\.(agent|mcp|memory|skills|subagents|tools)( |\.|$)" \
    services/ai-backend/src services/ai-backend/tests
```

Inverting the exit status (negation by `!`) so CI fails if any match appears.

---

## 7. Rollback plan

`git revert` the deletion commit. The directories reappear empty (no `.py` files were ever in them).

---

## 8. Implementation order within the PR

Single commit:

1. Re-run pre-flight greps as a sanity check.
2. `git rm -r services/ai-backend/src/agent_runtime/{agent,mcp,memory,skills,subagents,tools}`
3. Run full test suite locally.
4. Open PR.

---

## 9. Open follow-ups

- Should the optional CI grep step be added in a separate PR? Yes — keep this PR to one outcome.
- Should the audit doc be updated to reflect the three withdrawn findings? Yes — recommended in a follow-up edit, not in this PR's scope.
- Are there other "obviously safe" hygiene cleanups that turned out to be load-bearing under inspection? Anyone reviewing this PRD should note: the audit was wrong on 3 of 4 items here. Apply the same skepticism to other hygiene PRDs in this folder.

---

_Phase 2 PR. Independent of [P5](01-async-only-ports.md), [P7](06-citation-batching.md), [P8](07-cluster-boundary-moves.md), and [P9](08-service-consolidation.md). Land in any order within Phase 2._

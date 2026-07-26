# PRD-AR-H1 — External skill-package ingestion and quarantine

**Goal.** Import external skill content into a content-addressed quarantine, validate
its manifest/assets/provenance without executing package code, and release only a
reviewable skill draft to the publication workflow.

| Field           | Value                                                                        |
| --------------- | ---------------------------------------------------------------------------- |
| Status          | Draft for review                                                             |
| Primary owner   | `backend` skill registry and supply-chain services                           |
| UI impact       | Additive import and quarantine status flows through shared surfaces          |
| Runtime rollout | Ingestion does not make a skill runtime-visible                              |
| Depends on      | A2 artifact/blob patterns, A3 Operation Gateway, E1 accountability/lifecycle |
| Blocks          | AR-H2 skill draft/review/publish                                             |

## Implementer brief

Read before implementation:

1. `../README.md`.
2. `../../prds/PRD-A2-artifact-repository.md`.
3. `../../prds/PRD-A3-operation-gateway.md`.
4. `../../prds/PRD-E1-accountability-lifecycle.md`.
5. `services/backend/src/backend_app/contracts.py` (`SkillRecord`,
   `CreateSkillRequest`, and `InternalSkillBundle`).
6. `services/backend/src/backend_app/service.py` (`SkillRegistryService`).
7. `services/backend/src/backend_app/store.py` (`InMemorySkillStore` and
   `PostgresSkillStore`).
8. `services/backend/src/backend_app/app.py` skill routes.
9. `services/backend-facade/src/backend_facade/app.py` skill proxies.
10. `services/backend/src/backend_app/library/blob_store.py` for signed upload and
    opaque blob-ref patterns.
11. `services/ai-backend/src/agent_runtime/capabilities/skills/manifest.py`.
12. `services/ai-backend/src/agent_runtime/capabilities/skills/sources.py`.
13. `services/ai-backend/src/agent_runtime/capabilities/skills/policy.py`.
14. `packages/api-types/src/skills.ts` and the skill contracts in
    `packages/api-types/src/index.ts`.

Manifest parsing is needed in both deployables. Do not import `ai-backend/src` from
`backend`. Either implement the backend-side contract independently with golden
fixtures or promote only stable constants/contracts through an allowed package after a
service-boundary review.

## Problem statement

The product supports user-authored `SKILL.md` text and runtime loading, but it does not
have a safe intake pipeline for an archive, registry package, or pinned source tree.
Treating an external package as ordinary markdown would omit archive traversal,
symlink/device, decompression, secret, license, provenance, manifest, asset, and
supply-chain checks. Treating package installation as command execution would grant
unreviewed code execution during a content import.

External skill ingestion must end at a quarantined, inspectable draft. It must not
enable the skill, add tools, start a process, fetch runtime dependencies, or mutate an
active revision.

## Current implementation and predecessor contracts

- **[shipped]** Backend skills are tenant/user scoped, versioned, enable/disable capable, audited, and
  available to `ai-backend` through private cards/bundle endpoints.
- **[shipped]** Runtime manifest parsing validates frontmatter, names, descriptions, compatibility,
  allowed tools, size, and safe relative asset references.
- **[shipped]** Preloaded/system skills are distinguishable from user skills.
- **[shipped]** The backend already has opaque blob storage and signed upload patterns.
- **[shipped]** The facade derives identity from the verified session.
- **[depends on]** E1 provides audit, retention, deletion, and legal-hold requirements.

## Critical package-class boundary

A **skill bundle** is content: `SKILL.md` plus bounded referenced assets. It is parsed
and displayed but never executed during ingestion or loading.

Installer-shaped text is not itself a package classification. Community instructions
such as `npx skills add <repo>` may merely point to a declarative skill bundle. The
native import UX may parse such text as an untrusted locator hint, resolve an approved
pinned repository/URL/archive/already-installed-directory snapshot or future registry
metadata, and ingest the resulting bytes. It never runs the community installer,
package manager, lifecycle hooks, or repository scripts.

An **executable/runtime package** is classified from the resolved product bytes and
manifest: its product is a binary, container, lifecycle-driven program, or running MCP
server rather than a declarative `SKILL.md` bundle. Only an actual MCP-server product
belongs to J1's separately reviewed MCP registration/runtime isolation architecture.

Therefore:

- skill import requests may contain a command-shaped locator hint, but the parser emits
  only a canonical source descriptor and never an executable plan;
- unresolved/floating locator hints are rejected; resolved bytes must be pinned by full
  commit or content digest;
- packages whose resolved product is executable/runtime content are rejected as the
  wrong package class;
- `npx`/`uvx` strings in instructional prose are inert text and never run
  automatically;
- a future executable-package installer must use a separately reviewed sandbox,
  dependency lock, signature/provenance, network, secret, and permission design.

## Objectives

1. Accept bounded skill archives/uploads and approved pinned-source descriptors.
2. Produce a content-addressed immutable package and scan report.
3. Enforce archive, manifest, asset, secret, license, and executable-content policy.
4. Keep imported material quarantined until H2 review/publish.
5. Preserve source provenance and exact bytes across retries and review.
6. Support revocation and re-scan when scanner policy changes.

### Success measures

- Zero archive escape, symlink/device, decompression bomb, or command execution in the
  adversarial suite.
- 100% reproducible package digest for identical normalized input bytes.
- p95 scan completion below 10 seconds for a 10 MiB/500-file package excluding external
  transparency checks.
- Duplicate import of one digest creates one package and one caller-scoped import
  record.
- Runtime skill cards/bundles never include a quarantined/rejected package.

## Non-goals

- Publishing/enabling a skill, rolling back a revision, or agent-authored skill
  distillation.
- Executing package code, scripts, examples, tests, or setup commands.
- Installing MCP servers or package-manager dependencies.
- Resolving unpinned moving branches/tags as durable provenance.
- Automatically approving licenses or granting allowed tools.

## Interfaces consumed

- Existing verified identity and facade routing.
- Backend blob-store/signing patterns for bounded direct upload.
- Existing skill manifest semantics and runtime allowed-tool policy.
- E1 audit, retention, deletion, and legal-hold behavior.

## Interfaces exposed

### Public facade APIs

```text
POST /v1/skills/packages/upload-grant
POST /v1/skills/packages/import
GET  /v1/skills/packages/imports/{import_id}
GET  /v1/skills/packages/{package_id}/report
DELETE /v1/skills/packages/imports/{import_id}
```

Apps call these only through the facade. The backend body never trusts caller-supplied
org/user ids.

```text
SkillPackageImportRequest
  idempotency_key: string
  source:
    kind: upload
    blob_ref: string
    expected_sha256: string
  requested_scope: user | org
  expected_root?: string

SkillPackageImportResponse
  import_id: string
  package_id?: string                    # absent for wrong_package_class
  status: uploaded | scanning | quarantined | ready_for_review | rejected | cancelled
  package_digest: sha256
  scanner_policy_version: string
  safe_summary: string
  rejection_code?: wrong_package_class | invalid_archive | invalid_manifest |
                   policy_blocked | source_unverifiable
  remediation_target?: executable_mcp_package_intake
```

### Internal generated-package intake

```text
POST /internal/v1/skills/packages/agent-generated
```

The internal `agent-generated` endpoint is callable only by `ai-backend` with verified
service headers. It accepts an A2 immutable artifact/blob ref, expected digest, source
run/job refs, generator/prompt/policy revisions, requested scope ceiling, and
idempotency key. It never accepts inline bytes, commands, URLs, or identity in the body.
It produces the same immutable package and scanner report as external intake, with:

```text
source_kind: agent_generated
author_kind: agent
source_run_ref
source_job_ref
scope_ceiling
```

There is no privileged “trusted generator” bypass; generated content passes the exact
same archive, manifest, asset, secret, license, and executable-content controls.

Approved native-source intake may be added as typed sources:

```text
kind: registry_metadata | git | pinned_url | installed_directory_snapshot
canonical_source_id
immutable_revision                    # content digest or full commit SHA
expected_sha256
locator_hint_digest?                  # original command/URL text is not executed
```

The server resolves only configured registries/hosts through an SSRF-safe fetcher. A
branch, floating tag, arbitrary URL, or embedded credential is invalid.
An already-installed directory must arrive as an immutable, grant-backed snapshot/blob;
backend never accepts or dereferences a caller-supplied host path.

### Persistence

```text
skill_packages
  package_id, org_id, content_digest, blob_ref, format, byte_count, file_count
  manifest_digest, scanner_policy_version, scan_status
  created_at, scanned_at, revoked_at?

skill_package_imports
  import_id, package_id, org_id, user_id, source_kind
  source_locator_ref, immutable_revision?, requested_scope
  status, failure_code?, created_at, updated_at

skill_package_files
  package_id, normalized_path, content_digest, media_type, size_bytes
  classification: manifest | referenced_asset | unreferenced | executable | rejected

skill_package_findings
  finding_id, package_id, scanner_id, severity, code
  file_path_digest?, safe_message, blocking, created_at
```

Bodies/source credentials remain behind refs. Findings never store discovered secret
plaintext.

### Events

```text
skill.package.imported.v1
skill.package.scan_started.v1
skill.package.quarantined.v1
skill.package.ready_for_review.v1
skill.package.rejected.v1
skill.package.revoked.v1
```

Events carry ids, digests, scanner version, counts, status, and safe finding codes—not
file bodies, source URLs, credentials, or detected secret values.

## Design

### D1. Intake and immutable bytes

Uploads use a short-lived, size-bound grant and finalize with expected byte count and
SHA-256. Backend verifies the blob before creating the import. The original archive is
immutable and addressed by digest.

Source fetches, if enabled, resolve a full immutable revision, download once through a
restricted adapter, record provenance, and verify the expected digest. Redirects,
credentials, private networks, and oversized responses are rejected.

### D2. Archive normalization

Supported initial formats are `.zip` and optionally tar without external decompressors.
The extractor:

- normalizes paths as portable POSIX relative paths;
- rejects absolute, parent, empty, duplicate-after-normalization, Unicode-confusable,
  case-colliding, Windows-device, and overlong paths;
- rejects symlinks, hard links, devices, sockets, FIFOs, sparse amplification, and
  encrypted entries;
- limits compressed bytes, expanded bytes, ratio, file count, depth, and per-file size;
- writes only into an isolated content-addressed staging directory/object namespace;
- never invokes an archive-provided binary.

Extraction is deterministic. Package digest remains the digest of original bytes;
manifest and file-tree digests identify normalized content.

### D3. Package shape

Exactly one selected root must contain `SKILL.md`. Multiple roots require the user to
select `expected_root` before scanning can finish; the server never guesses based on a
marketing name.

Only files referenced safely by `SKILL.md` are candidates for the released bundle.
Unreferenced files are reported and excluded by default. Referenced paths must stay
inside the selected root.

### D4. Scanner pipeline

The versioned scanner chain performs:

1. archive/path/type validation;
2. UTF-8/frontmatter/manifest parsing;
3. stable name/description/license/compatibility/allowed-tool validation;
4. asset reference closure and media/size validation;
5. executable and lifecycle-script classification;
6. secret/token/private-key pattern scanning with no plaintext retention;
7. license metadata and policy evaluation;
8. instruction-risk analysis for review prioritization;
9. source provenance/signature/transparency verification when supported.

Instruction-risk analysis is advisory; structural controls enforce safety. A skill may
contain shell examples as prose, but executable files/scripts are excluded or blocking
according to policy. Nothing is run to determine behavior.

### D5. Status machine

```text
uploaded → scanning → quarantined → ready_for_review
                    ↘ rejected
uploaded|scanning|quarantined → cancelled
ready_for_review → revoked
```

`quarantined` means the immutable package and report exist but scanner/admin/user
resolution is pending. `ready_for_review` means the content passed ingestion checks; it
is still not published or enabled. H2 consumes only `ready_for_review`.

Transitions use compare-and-set versioning and append audit in the same transaction.

### D6. Policy and findings

Blocking findings include archive escape, executable package class, embedded
credentials, invalid manifest, unsafe/missing assets, disallowed license, unresolved
source digest, signature failure when signature is required, and configured high-risk
content.

If the resolved product itself is an executable package, lifecycle-driven runtime, or
MCP server bundle, it is rejected with the typed code `wrong_package_class`. An
`npx`/`uvx` locator that resolves to a declarative skill is not rejected merely for its
syntax. The response may offer the UI-only remediation target
`executable_mcp_package_intake` only when the product is a running MCP server and the
separately reviewed J1 flow is available. H1 never forwards bytes, creates an
executable-package record, installs a dependency, or starts a process on the caller's
behalf.

An admin policy may be stricter by tenant. A user cannot suppress a blocking
organization finding. Non-blocking warnings remain visible through H2 review.

### D7. De-duplication and idempotency

Package metadata, scan status, findings, source linkage, report IDs, and package IDs are
strictly tenant scoped. The visible uniqueness constraint is
`(org_id, content_digest)`; authorization and row-level tests always lead with
`org_id`. A lower storage layer may deduplicate encrypted blob bytes by digest, but that
deduplication sits below authorization and exposes no cross-tenant package id,
existence/status, timing distinction, finding reuse, reference count, or deletion
behavior.

Same caller/idempotency key and request digest returns the same import. Same key with a
different digest returns conflict. Visible scanner output is keyed by org, package
digest, and scanner policy version; any content-safe scanner computation reused below
that layer is copied into a new tenant-owned report without observable linkage.

### D8. No runtime visibility

The existing `skills` table/internal card endpoints remain active-revision-only.
Package/import/report tables are not queried by runtime card loading. No path from scan
completion calls `create_skill`, sets `enabled`, or writes a runtime virtual path.

An architecture test fails if the ingestion service imports/obtains the runtime skill
provider, MCP launcher, subprocess API, or executor registry.

## Persistence, retention, deletion, and legal hold

- Original package bytes and normalized released files are content-addressed refs.
- Cancelled/rejected imports retain reports for the configured security/audit period,
  then delete unreferenced package bodies.
- Deleting an import removes caller linkage; shared content is collected only when no
  imports/drafts/revisions/holds reference it.
- H2 publication creates immutable references that prevent collection.
- Revocation prevents new draft/publication and marks descendants for review; it does
  not silently rewrite active skill history.
- Legal hold retains package/report/audit linkage for the held tenant without making the
  package runtime-visible.
- Account/org deletion removes import links, source locators, package refs, findings,
  and scan jobs subject to hold.
- Physical blob collection uses an internal unobservable reference count; deleting one
  tenant's package metadata can neither delete another tenant's authorized bytes nor
  reveal that another reference exists.

## Authorization, supply chain, privacy, and security

- User imports target user scope; org scope requires the existing/admin-defined role and
  explicit policy.
- Identity comes from verified session/service headers, never payload.
- Source credentials live in the token vault and are never stored in locators/events.
- Fetchers use strict SSRF/DNS/redirect policy and an allowlist of source adapters.
- Scanners run with no network, no secrets, read-only package input, bounded resources,
  and a fresh isolated working directory.
- Scanner dependencies/images are pinned and recorded by version/digest.
- Findings and audit contain safe codes, not source bodies or detected secrets.
- Package text is untrusted and never included in system logs or executed.
- `agent_generated` packages receive no weaker scanner, authorization, retention, or
  publication treatment than uploads.

## Performance and capacity

- Initial limits: compressed 10 MiB, expanded 50 MiB, 500 files, depth 20, individual
  file 10 MiB, `SKILL.md` 1 MiB, referenced-assets total 25 MiB.
- Upload finalize p95 under 1 second after bytes arrive.
- Scan p95 under 10 seconds at maximum normal package; hard deadline 60 seconds.
- Tenant concurrent scans default 2; process pool bounded globally.
- Extraction/scanning is `O(total archive entries + expanded bytes)`.
- Reports list at most 200 findings; overflow is summarized by code/severity.

## Failure, idempotency, and recovery

- Scan jobs are durable, claim-before-work, leased, and retry typed infrastructure
  failures only.
- Parser/policy/rejection results are deterministic and non-retryable for the same
  policy version.
- Crash during extraction leaves no visible normalized package until atomic finalize.
- Stuck leases are reclaimed; partial staging content is janitor-cleaned.
- Cancellation stops pending work and prevents `ready_for_review`.
- Re-scan under a new policy creates a new report/status generation without mutating old
  audit evidence.
- Revoked source/signature marks package revoked and blocks future use.

## Metrics

- `skill_package_import_total{source_kind,outcome}`
- `skill_package_scan_duration_ms{scanner_version,outcome}`
- `skill_package_findings_total{code,severity}`
- `skill_package_bytes{phase=compressed|expanded|released}`
- `skill_package_dedup_total{result}`
- `skill_package_scan_queue_age_seconds`
- `skill_package_cleanup_pending`

Labels contain no package/source/name/path/user identifiers.

## Rollout and backout

1. Land tables/contracts/scanner fakes with public routes disabled.
2. Enable upload-only import for internal users; all results remain quarantined.
3. Enable `ready_for_review` output into H2 after its review UI/API exists.
4. Add approved registry metadata/Git/pinned-URL/directory-snapshot adapters separately
   by source kind; command-shaped hints remain parser-only.
5. Enable org-scoped intake only after admin policy and audit export are complete.

Backout disables new grants/imports and drains or cancels scan jobs. Existing reports
and package refs remain readable for review/audit; no active skill is changed.

## Implementation slices

1. Add contracts, migration, stores, idempotency, and golden fixtures.
2. Add signed upload/finalize and immutable package service.
3. Implement safe extractor and package-shape validation.
4. Implement versioned scanner chain and isolated worker.
5. Add import/report routes through facade and shared API types.
6. Add audit/events/metrics/retention/janitor/re-scan/revocation.
7. Add external and `agent_generated` H2 handoff contracts and architecture
   no-execution gate.

## Test plan

### Archive and package security

- Absolute/parent/case-collision/confusable/device paths, symlink/hardlink/FIFO/socket,
  sparse/encrypted/recursive archives, zip/tar bombs, huge counts/depth/headers.
- Multiple roots, missing/malformed manifest, unsafe/missing assets, binary manifest,
  executable files, lifecycle metadata.

### Supply chain and privacy

- Unpinned/floating sources, digest mismatch, signature/revocation, SSRF/DNS rebinding,
  redirects, embedded credentials, secret scanning with no plaintext persistence.
- Scanner has no network/secrets/subprocess access and cannot affect runtime skills.
- `npx`/`uvx` locator hints resolve declarative bundles without execution; resolved
  executable/MCP products return `wrong_package_class`, and no process, dependency
  install, byte forwarding, or automatic J1 job is created.
- Agent-generated intake uses the same scanner version and cannot bypass a blocking
  finding.

### Authorization and lifecycle

- User/org role, cross-tenant ids/digests, enumeration timing, duplicate idempotency,
  cancellation, account deletion, legal hold, ref-count collection.
- Cross-tenant scan reuse, physical-blob reference count, report status, timing, and
  deletion behavior expose no existence oracle.

### Recovery and capacity

- Crash at upload/extract/scan/finalize, stuck lease, duplicate worker, rescan, janitor.
- Maximum valid package and adversarial expansion resource bounds.

## Definition of done

- [ ] External skill bundles enter immutable quarantine and are never executed.
- [ ] Archive, manifest, asset, secret, license, provenance, and executable-class checks
      are enforced with versioned reports.
- [ ] Native pinned repo/URL/archive/directory/registry import resolves declarative
      bundles without executing installer code.
- [ ] Resolved executable/runtime products are rejected with
      `wrong_package_class`; only actual MCP-server products may show a J1 UI hint, with
      no automatic routing/copying/execution.
- [ ] Only `ready_for_review` packages can reach H2, and none are runtime-visible.
- [ ] Idempotency, tenant isolation, audit, deletion, hold, recovery, and capacity gates
      pass.
- [ ] Architecture tests prove ingestion cannot launch code or publish/enable a skill.

## Guardrails

- No package execution, lifecycle scripts, dependency installation, or dynamic imports.
- No floating source revision.
- No archive extraction outside isolated content-addressed staging.
- No scan completion that publishes or enables a skill.
- No source URL, credential, secret finding, or file body in logs/events.

## Open decisions

- Initial upload archive formats: ZIP only or ZIP plus restricted tar.
- Signature/transparency providers required for organization-scoped registry packages.

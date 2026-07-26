# PRD-AR-J2 — Multimodal media capability registry

**Goal:** Give the runtime one policy-aware way to discover, select, execute, and evaluate image, audio, video, and document capabilities while preserving artifacts, provenance, budgets, and safe prompt boundaries.

## Metadata

| Field                   | Value                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Status                  | Optional / proposed                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| Wave                    | J — advanced capability platform                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| Primary owner           | AI backend capability platform                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| Supporting owners       | Backend provider policy, artifact services, surface renderers                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| Depends on              | [A1 artifact effect contracts](../../generative-surfaces-v2-1/prds/PRD-A1-artifact-effect-contracts.md), [A2 artifact repository](../../generative-surfaces-v2-1/prds/PRD-A2-artifact-repository.md), [B1 agent-authored artifacts](../../generative-surfaces-v2-1/prds/PRD-B1-agent-authored-artifacts.md), [B2 artifact renderers and editors](../../generative-surfaces-v2-1/prds/PRD-B2-artifact-renderers-editors.md), [D3 sandbox adapter](../../generative-surfaces-v2-1/prds/PRD-D3-sandbox-adapter.md), [D4 browser adapter](../../generative-surfaces-v2-1/prds/PRD-D4-browser-adapter.md), [F3 policy-aware capability discovery](./PRD-AR-F3-policy-aware-capability-discovery.md), [E1 accountability lifecycle](../../generative-surfaces-v2-1/prds/PRD-E1-accountability-lifecycle.md) |
| Primary success measure | The runtime chooses a policy-valid media capability with bounded selection overhead and every output is a traceable, renderable artifact                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |

## Implementer brief

Read:

- `services/ai-backend/src/agent_runtime/capabilities/`
- `services/ai-backend/src/agent_runtime/execution/`
- `services/ai-backend/src/agent_runtime/persistence/`
- `services/backend/src/backend_app/provider_keys/`
- `services/backend/src/backend_app/`
- `packages/surface-renderers/`
- `packages/api-types/`
- A1, A2, B1, B2, D3, D4, F3, and E1

Browser screenshot capture already belongs to D4. Artifact storage and rendering already belong to A1/A2 and B1/B2. This PRD adds capability metadata, routing, media normalization, execution envelopes, and quality evaluation; it must not create a second blob store, screenshot mechanism, renderer system, or unrestricted media tool.

## Desktop-first deployment and storage contract

The launch composition is the shipped `single_user_desktop` profile: Electron main supervises `backend`, `ai-backend`, `backend-facade`, and the local data services on loopback; the renderer still calls only the facade. This is a local application boundary, not a mandatory cloud dependency.

- Runtime-owned run/event/citation/artifact state must use existing `RuntimePorts`. Desktop defaults to the single-writer file adapter under `<userData>/agent-data/v1` with the in-process worker; tests use in-memory and future hosted deployments may use Postgres.
- Product configuration already owned by `backend` may use its existing embedded local Postgres database. This PRD must not add another database, daemon, queue, or network hop merely for desktop.
- Authorization is expressed as the signed-in local user, device capability grants, project/conversation scope, and provider credentials. Existing internal organization identifiers remain compatibility partition keys; they are not exposed as B2C team or administrator concepts.
- A future consumer web or opt-in sync product may add remote adapters behind the same ports and contracts. Sync, team administration, fleet policy, and always-online availability are not desktop launch dependencies.
- Feature flags resolve locally, work offline wherever no external provider is intrinsically required, and include a bounded disk/CPU/memory budget plus an immediate local backout path.

## Problem statement

Media tasks differ substantially: inspect an image, extract text from a scan, transcribe audio, synthesize speech, generate an image, sample a video, transform an asset, or render a final result. Providers support different modalities, limits, costs, regions, safety controls, latency, and output guarantees. Exposing all provider tools directly increases prompt tokens and wrong-tool calls, while ad hoc wrappers produce inconsistent artifact handling, privacy enforcement, and quality measurement.

The runtime needs a governed registry of media capabilities and a deterministic router that selects a small set of eligible options from task intent, media metadata, user policy, model compatibility, cost, and quality history.

## Current state and strengths to preserve

- A1/A2 define artifact manifests, repositories, integrity, lifecycle, and access.
- B1/B2 define render plans and surface-renderer selection.
- D3 defines isolated compute for transformations.
- D4 defines browser state and screenshot media handling.
- F3 defines policy-aware capability discovery rather than placing every tool in every prompt.
- Backend provider-key storage keeps plaintext keys out of requests and logs.

## Objectives and outcomes

1. Describe media capabilities in a provider-neutral, versioned registry.
2. Normalize input metadata and store bytes as governed artifacts before model use.
3. Select only capabilities compatible with modality, task, policy, budget, and region.
4. Pass references and bounded derivatives instead of unbounded bytes or transcripts.
5. Persist provenance from source through transformation and generation.
6. Evaluate output quality, safety, latency, and cost per capability version.
7. Make provider fallback explicit, bounded, and reproducible.

## Scope

- Image understanding, OCR, image generation/editing, transcription, speech synthesis, audio understanding, video sampling/understanding, and safe media transformation
- Capability cards, provider adapters, user enablement, routing, fallback, and health
- Media sniffing, validation, normalization, derivatives, and artifact linkage
- Execution jobs, progress events, cancellation, budgets, safety, provenance, and evaluation
- Prompt-facing compact media handles and result manifests

## Non-goals

- Reimplementing browser screenshots from D4
- Replacing artifact storage, render plans, or surface renderers
- Building a professional nonlinear audio/video editor
- Supporting every codec or provider in the first release
- Sending private media to an unapproved provider
- Embedding raw binary data, full video, or unlimited transcripts in prompts
- Treating extracted text as trusted instructions

## Interfaces consumed

- A1/A2 artifact manifests, byte storage, access checks, digests, retention, and deletion
- B1/B2 artifact authoring, render plans, renderer selection, and edit handoff
- D3 sandbox jobs for deterministic media inspection and transformation
- D4 browser screenshot artifact and provenance contracts
- F3 local-profile-policy-aware capability discovery and compact prompt projection
- Backend provider enablement and vault-backed BYOK references
- Existing AI-backend run events, cancellation, budgets, and artifact references

## Interfaces exposed

Internal capability APIs:

```text
GET  /internal/v1/media-capabilities
POST /internal/v1/media-assets/inspect
POST /internal/v1/media-jobs
GET  /internal/v1/media-jobs/{job_id}
POST /internal/v1/media-jobs/{job_id}/cancel
POST /internal/v1/media-capabilities/route
```

Product upload, artifact retrieval, and rendering continue through A1/A2 and B1/B2 facade contracts. Provider configuration remains a backend settings concern.

## Core contracts

```text
MediaCapabilityCard
  capability_id
  version
  provider_id
  operation: understand | extract | generate | transform | synthesize
  input_modalities[]
  output_modalities[]
  mime_types[]
  dimensional_limits
  duration_limits
  locale_support[]
  feature_flags[]
  execution_class: inline | async | sandbox
  data_residency[]
  retention_behavior
  safety_controls[]
  cost_model
  latency_class
  quality_profile
  deterministic_level
  health

MediaAssetDescriptor
  artifact_id
  digest
  declared_mime
  detected_mime
  byte_size
  width
  height
  duration_ms
  frame_rate
  channels
  sample_rate
  page_count
  codec
  sensitivity
  source_type
  provenance_ref

MediaJob
  media_job_id
  profile_id
  actor_id
  operation
  capability_id
  capability_version
  input_artifact_refs[]
  normalized_input_refs[]
  parameters
  budget
  policy_snapshot_ref
  status: queued | validating | running | succeeded |
          failed | cancelled | quarantined | indeterminate
  output_artifact_refs[]
  usage
  safety_result
  provenance_ref
  error_code
```

Capability parameters use operation-specific closed schemas. Provider-native arbitrary parameter maps are not accepted from model output.

## Detailed design

### 1. Registry and adapter conformance

Each adapter publishes signed or build-pinned cards. Registration validates schema, supported MIME types, limits, cancellation behavior, data retention, residency, safety settings, and cost formulas.

Adapters pass conformance fixtures for deterministic metadata, artifact reference handling, timeout, cancellation, usage accounting, safe errors, and output manifests. Unhealthy versions are removed from routing but remain in historical provenance.

### 2. Media ingestion and normalization

Before routing, ingestion:

1. writes bytes through A2;
2. computes a digest;
3. compares declared and detected MIME;
4. applies size and decompression-bomb limits;
5. performs malware and structural checks;
6. extracts bounded metadata;
7. strips or preserves metadata according to policy; and
8. creates safe derivatives where required.

Inputs may come from user uploads, connectors, D4 screenshots, D3 outputs, or prior artifacts. A source type never grants additional trust.

### 3. Intent and eligibility

Runtime middleware compiles task intent into a closed `MediaCapabilityRequest`: operation, input descriptors, required output, quality tier, latency preference, budget, locale, privacy/residency constraints, and deterministic requirements.

The eligibility filter removes capabilities that violate MIME, dimensions, duration, provider enablement, key availability, region, retention, safety, accessibility, cost, or health constraints. This deterministic filter runs before model-visible selection.

### 4. Routing and prompt optimization

The router ranks the remaining small set using task fit, validated quality, predicted cost, latency, reliability, and user preference. It returns one primary and at most two policy-valid fallbacks plus concise reason codes.

The model sees normalized capability families or the selected operation schema, not every provider tool. Registry lookup is cached by user policy revision, registry revision, operation, and media shape. Secret/key changes invalidate eligibility without exposing the key.

### 5. Understanding and extraction

Image/video/audio understanding produces structured observations with source spans:

- image regions or page coordinates;
- video time ranges and sampled frame references;
- audio time ranges and speaker labels where supported; and
- OCR text blocks with confidence and coordinates.

Derived text is stored as an artifact or bounded result and clearly marked untrusted. The harness may quote or reason over it, but it cannot treat media-embedded instructions as system policy.

### 6. Generation and transformation

Generation and editing requests use typed briefs and reference artifacts. Outputs are written directly into A2 with provider/model/version, parameters, source digests, safety result, usage, and timestamps.

Deterministic transformations such as resizing, transcoding, waveform extraction, thumbnailing, and frame sampling should use D3 when cheaper and safer than a generative provider.

### 7. Audio and video execution

Long audio/video jobs execute asynchronously, checkpoint progress, and support cancellation. The system samples or segments media under a declared plan; it does not send an entire long asset simply because a provider accepts it.

Segment results retain timecode linkage and are merged by a deterministic adapter where possible. Model synthesis receives bounded segment summaries and can retrieve cited segments on demand.

### 8. Safety, privacy, and accessibility

Policy can require content classification before external processing or generation. Routing respects local user controls, consent, age restrictions, biometric restrictions, copyright policy, and provider retention settings.

Generated media records safety decisions and disclosure metadata. User-visible outputs include alt text or transcript/caption artifacts when policy requires them. Metadata stripping must not remove provenance needed for audit.

### 9. Fallback and reconciliation

Fallback occurs only for retryable capability failures and only among preapproved candidates. It is bounded by job budget and records the reason. A different provider's output is never represented as the original provider's result.

If a provider accepts a generation request but the response is lost, the job becomes `indeterminate` unless the provider supports idempotency or status reconciliation. The runtime does not spend again blindly.

### 10. Quality feedback

Collect privacy-safe automated and human signals:

- OCR/transcription accuracy on consented eval sets;
- grounding and citation validity;
- generation instruction adherence;
- artifact validity and renderability;
- user retry/edit/accept behavior;
- latency, cost, safety false-positive/negative review; and
- provider/model regression by capability version.

Evaluation data is separate from user content unless explicit opt-in allows its use.

## Ownership and service boundaries

| Responsibility                                     | Owner                       |
| -------------------------------------------------- | --------------------------- |
| Capability registry, routing, adapters, media jobs | AI backend                  |
| Provider enablement, BYOK references, user policy  | Backend                     |
| Artifact manifests, bytes, provenance              | A1/A2 owners                |
| Deterministic isolated transformation              | D3 owner                    |
| Browser screenshots                                | D4 owner                    |
| Render plans and presentation                      | B1/B2 and surface renderers |
| Public API aggregation                             | Backend facade              |

No service imports a sibling deployment's source. Provider keys are resolved through authenticated internal contracts and never persisted in media jobs.

## Persistence, retention, and deletion

- Capability cards and versions are durable configuration with audit history.
- Media jobs store references, digests, usage, safety, and provenance; bytes remain in A2.
- Intermediate derivatives have explicit parent linkage and shorter default retention.
- Delete traverses originals, eligible derivatives, OCR/transcript artifacts, embeddings, job rows, caches, and render references.
- Local backup/restore policy preserves selected artifacts and provenance without
  re-enabling external processing after a user delete.
- Provider-side retention behavior is recorded per execution and exposed to users.

## Authentication, authorization, security, and audit

- Derive the local profile/actor from verified context and authorize every
  artifact read.
- Resolve provider keys just in time and redact them from jobs, events, traces, and errors.
- Validate file structures in isolated processes; codec/parser crashes cannot compromise the runtime.
- Apply SSRF controls to remote imports and never let a model provide backend-fetchable private addresses.
- Bound archive expansion, image pixels, page counts, duration, frames, samples, transcript size, and generated variants.
- Audit upload/import, inspect, route, provider selection, process, fallback, safety decision, output, export, and delete.
- Raw sensitive media and extracted text are excluded from ordinary logs and evaluation by default.

## Performance and capacity budgets

- Registry eligibility and route: p95 under 20 ms on cache hit, under 100 ms on miss.
- Media metadata inspection: p95 under 500 ms for supported ordinary files.
- Job admission: p95 under 250 ms excluding upload.
- First progress event for async work: p95 under 2 seconds.
- Routing is `O(C)` over already indexed eligible capability cards, where `C` is the small operation/modality candidate set; it must not load all tools or providers into the model context.
- Prompt media inventory has hard item, byte, frame, page, duration, and token caps.
- Reuse safe derivatives by source digest plus normalization policy/version.

Provider processing latency has capability-specific SLOs. The job contract exposes estimates rather than forcing all modalities into one timeout.

## Failure, idempotency, and recovery

- Job creation is idempotent by profile, operation, input digests, normalized parameters, and caller key where reuse is safe.
- Non-deterministic generation does not reuse outputs unless the caller explicitly requests the prior job.
- Async claims use leases and checkpointed provider request IDs.
- Partial outputs are quarantined until manifest validation and artifact commit finish.
- Provider outage trips health and reroutes only within approved fallbacks.
- Policy, key, artifact, safety, or audit dependency failure fails closed.
- Cancellation records whether provider processing could be stopped and cleans eligible intermediates.

## Observability and quality gates

Metrics:

- route cache hit, candidate count, selection reason, and route latency;
- job latency, queue age, cancellation, failure, fallback, and indeterminate rate;
- bytes/pages/duration/frames processed;
- provider/model cost and utilization;
- artifact validation and render failure;
- OCR/transcription/grounding quality;
- safety actions and review outcomes; and
- derivative reuse and prompt payload reduction.

Trace lineage is `source artifact → normalization/derivative → media job/capability version → provider request or D3 job → output artifact → render plan`.

Release gates:

- every output has valid artifact and provenance manifests;
- no ineligible provider receives media;
- raw bytes and secrets do not appear in prompts, logs, or ordinary events;
- media-embedded instructions cannot alter harness policy;
- provider fallback stays within budget and policy;
- malformed-media, decompression, parser-isolation, deletion, and
  local-profile-isolation tests pass; and
- routing improves tool-schema prompt size without reducing benchmark quality.

## Rollout and backout

1. Ship registry and routing in shadow mode over existing media paths.
2. Enable image inspection and OCR with one adapter each.
3. Add transcription and deterministic transformations.
4. Add image generation/editing with artifact provenance and safety review.
5. Add bounded video sampling/understanding.
6. Expand providers and local-user policy choices after quality and privacy gates.

Backout disables a capability version or provider in the registry, drains/cancels jobs, preserves artifacts and provenance, and routes only to previously approved alternatives. D4 screenshots and existing artifact rendering continue independently.

## Implementation slices

1. Capability-card schema, registry, policy join, and cache
2. Media descriptor, safe inspection, and A2 ingestion integration
3. Router, typed operation requests, and prompt-facing handles
4. Image understanding/OCR adapters and evidence spans
5. D3 transformation adapter and derivative cache
6. Audio transcription/synthesis and async jobs
7. Image generation/editing, provenance, and safety
8. Video sampling/understanding, evaluation, and rollout controls

## Test plan

- Unit: card validation, eligibility, ranking, cache invalidation, budget
- Media security: MIME mismatch, polyglots, bombs, malformed codecs, SSRF
- Contract: provider adapter conformance, cancellation, usage, artifact manifests
- Integration: upload through understanding/generation to rendered surface
- Quality: OCR, transcription, grounding, generation adherence, accessibility
- Governance: disabled provider, missing key, residency, retention, safety policy
- Fault injection: provider timeout, lost response, artifact commit failure, worker crash
- Retention: derivative cascade, user deletion, local backup/restore policy,
  and provider-retention record
- Performance: large registry, long media segmentation, bounded prompt payload

## Definition of done

- Eligible media operations are discovered through one versioned registry.
- Routing is deterministic before optional model choice and remains within policy and budget.
- All media bytes, derivatives, and outputs use governed artifact contracts.
- Every result records capability version, provider or sandbox execution, source digests, usage, safety, and provenance.
- Browser screenshots, transforms, storage, and rendering reuse D4, D3, A1/A2, and B1/B2.
- Security, privacy, retention, quality, and performance gates pass for every enabled modality.

## Guardrails

- Media is untrusted content, never harness policy.
- Capability metadata is code-reviewed configuration, not model-authored tool schema.
- No raw secrets, unrestricted URLs, host file paths, or unbounded binary prompt content.
- No provider outside user policy or declared residency/retention limits.
- No silent fallback, hidden generation spend, provenance loss, or unsafe partial artifact.
- Unsupported or unhealthy capability versions fail closed and remain explainable.

## Open decisions

1. Which initial providers and local processors satisfy each operation.
2. Which quality signals may be learned from user usage under explicit opt-in.
3. Whether speech synthesis and image generation require per-job user confirmation.
4. Which provenance and disclosure standards are required for generated media.
5. Maximum default duration, pages, pixels, frames, variants, and provider fallback count.

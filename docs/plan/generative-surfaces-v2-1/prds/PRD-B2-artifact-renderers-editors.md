# PRD-B2 — Fixed artifact renderers and editors 🎨

**Goal.** Render and edit canonical code, document, dataset, and file artifacts with
pre-built UI-kit components, safe previews, exact downloads, immutable revisions,
optimistic concurrency, and honest raw fallback. No artifact content is executed.

## Implementer brief

Read:

1. `../00-overview.md` §§3.2, 6, 7.B, 7.E.
2. `../01-sdr.md` §§8.3–8.4 and 12.
3. `PRD-A2-artifact-repository.md` and
   `PRD-B1-agent-authored-artifacts.md`.
4. `packages/surface-renderers/src/archetypes/DocRenderer.tsx`.
5. `packages/surface-renderers/src/` renderer registry and safety tests.
6. `packages/chat-surface/src/thread-canvas/TcSurfaceMount.tsx`.
7. `packages/chat-surface/src/destinations/run/RunDestination.tsx`.
8. `packages/chat-transport/src/transport.ts`.
9. `packages/design-system/src/styles.css`.

Use `packages/chat-surface` and `packages/surface-renderers` as SSOT. Both hosts bind
ports; neither host duplicates the artifact UI.

## Context

The LLM supplies content and metadata. Fixed renderers provide visual consistency,
safety, speed, and editing mechanics. This is the “pre-defined components” part of
generative UI: the shape can vary within a schema, but the model never emits React,
HTML, CSS, event handlers, or executable view code.

## Interfaces consumed

- A2 metadata/content/revision APIs and ETags.
- B1 artifact kinds, media types, presentation decision.
- Existing surface-renderer and chat-surface registries.
- Existing design system and host-injected Transport/download/clipboard ports.

## Interfaces exposed

New shared renderer subjects:

```text
artifact-code://<artifact_id>@<revision>
artifact-document://<artifact_id>@<revision>
artifact-dataset://<artifact_id>@<revision>
artifact-file://<artifact_id>@<revision>
```

New components:

```text
packages/surface-renderers/src/artifacts/
  ArtifactRenderer.tsx
  CodeArtifactRenderer.tsx
  DocumentArtifactRenderer.tsx
  DatasetArtifactRenderer.tsx
  FileArtifactRenderer.tsx
  RawArtifactFallback.tsx

packages/chat-surface/src/artifacts/
  ArtifactFrame.tsx
  ArtifactEditor.tsx
  ArtifactRevisionHistory.tsx
  ArtifactDownloadAction.tsx
```

New transport methods mirror A2 routes and use streams/blobs rather than JSON base64.

## Design

### D1. Renderer selection

Selection is deterministic:

1. artifact kind;
2. validated media type;
3. bounded content inspection only for safe formatting, never authorization;
4. raw fallback.

Unsupported or malformed content does not trigger another model call automatically.
The user may request “Suggest a shape” for supported datasets/documents through the
existing shaping flow, but failure remains raw/honest.

### D2. Code renderer

Capabilities:

- line-numbered virtualized text;
- language label from metadata with safe fallback;
- copy exact bytes as decoded text only when valid;
- download original bytes;
- search, wrap toggle, and bounded syntax highlighting;
- edit mode with revision diff;
- visible binary/encoding warning when not UTF-8 text.

Security:

- never execute/import/eval code;
- never render HTML from code;
- syntax highlighter runs without plugins loaded from content;
- no clickable `javascript:`/custom URI;
- cap highlighted bytes/lines; large files use plain virtualized text.

### D3. Document renderer

- Markdown is parsed by the existing hardened Markdown path.
- Raw HTML is disabled or sanitized with one reviewed policy.
- Images/links follow existing safe URL policy; no credential-bearing fetch.
- Headings, lists, tables, code blocks, and task lists use design-system styles.
- Edit mode preserves source text; preview is derived.
- Download returns exact original bytes.

Do not turn a document into arbitrary embedded UI.

### D4. Dataset renderer and CSV fidelity

Supported initial formats:

- CSV;
- TSV;
- bounded JSON array of objects.

Parsing:

- RFC 4180-compatible quoting;
- preserve delimiter, newline convention, BOM, header order, empty cells, and quoted
  values where possible;
- detect encoding with an explicit fallback, never silently corrupt;
- cap preview rows/columns/cell bytes;
- virtualize table;
- show total rows only when known without full unbounded materialization.

Editing:

- cell edits create an in-memory patch and then a complete new artifact revision;
- preserve untouched cell bytes/quoting through a lossless parser when feasible;
- if exact round-trip cannot be guaranteed, show a fidelity warning before save;
- formula-like cells beginning `=`, `+`, `-`, `@` receive a spreadsheet-injection
  warning on download/export; do not silently mutate the canonical artifact;
- user may choose a safe-export transform that creates a new revision/file.

The renderer never executes formulas.

### D5. File renderer

For unsupported/binary media:

- filename, media type, byte size, digest abbreviation, revision, author;
- bounded hex/text preview only for allow-listed safe types;
- exact download;
- Promote/Save actions supplied by host ports;
- no inline browser navigation to executable content.

Images/PDF/audio may use existing safe product viewers only after their own security
contracts; otherwise use file metadata/raw fallback.

### D6. Editing and optimistic revisions

Editing uses:

```text
GET artifact metadata/revision
GET content with ETag
local editor buffer
POST revision with parent_revision and If-Match
```

On 409:

- do not overwrite;
- retain local buffer;
- show “A newer revision exists”;
- offer view diff, copy local changes, or rebase manually;
- no automatic merge for code/datasets in this PR.

Save creates an internal artifact revision and needs no external-write approval.
“Save to workspace/connector” is a separate effect action.

### D7. Revision history

Show:

- revision number/time/author;
- source attribution;
- content digest abbreviation;
- compare-to-current;
- restore as a new revision, never mutate history.

History is paginated. Content is fetched only when selected.

### D8. Loading, failure, and raw fallback

States:

- metadata skeleton;
- content streaming/loading;
- rendered;
- too large for preview;
- unsupported/raw;
- deleted/not found;
- transient fetch failure with retry.

An artifact tab remains identifiable while content is loading. A failed renderer cannot
blank the canvas or remove download access.

### D9. Accessibility

- tab/surface title is announced;
- editor has programmatic language/filename labels;
- table exposes grid semantics without trapping keyboard focus;
- diff has added/removed text alternatives, not color alone;
- action buttons have stable labels;
- focus returns predictably after save/conflict dialogs;
- large virtualized views remain navigable by keyboard.

### D10. Host ports

`packages/chat-surface` receives:

- Transport;
- download/blob sink;
- clipboard;
- optional open-external;
- notification/toast.

No package code uses bare `fetch`, `window`, `localStorage`, Electron IPC, or Node APIs.
Web and desktop adapters implement these ports independently.

## Implementation plan

1. Add artifact route types/guards to `packages/api-types`.
2. Add streaming methods to `packages/chat-transport`.
3. Implement raw/file renderer first.
4. Implement code and document renderers.
5. Implement dataset parser/renderer with fidelity fixtures.
6. Add ArtifactFrame/editor/revision history.
7. Register subject URI adapters in the shared surface registry.
8. Wire both host ports.
9. Add design-parity regions and accessibility tests.
10. Add performance fixtures at preview limits.

## Test plan

### Renderer safety

- hostile HTML/script remains inert;
- malicious URI schemes are blocked;
- code never executes;
- CSV formula cells never execute;
- unsupported binary always retains exact download.

### Fidelity

- golden CSV corpus round-trips quotes, commas, CRLF/LF, BOM, multiline cells,
  Unicode, empty trailing columns, duplicate headers;
- edited CSV changes only intended logical cells or displays fidelity warning;
- download digest equals server revision digest;
- Markdown source survives edit/save.

### Revision behavior

- exact parent revision saves;
- stale save gets 409 and keeps local buffer;
- restore creates a new revision;
- concurrent editors never overwrite one another silently.

### UI/accessibility/performance

- loading/raw/error/deleted states;
- keyboard navigation and screen-reader labels;
- virtualized 100k-row fixture stays within agreed memory/render budget;
- 0 HIGH design drift;
- shared UI runs in web and desktop.

## Definition of done

- [ ] Four artifact kinds have fixed safe renderers.
- [ ] Unsupported/oversize content has raw metadata and exact download.
- [ ] Editors create immutable revisions with conflict handling.
- [ ] CSV fidelity and formula warnings are test-pinned.
- [ ] No artifact content can execute code/UI.
- [ ] Web and desktop use the same shared components.
- [ ] UI and standard DoD pass.

## Out of scope

- Collaborative real-time editing.
- Automatic semantic merge.
- Arbitrary model-authored renderer code.
- Direct external/local save execution.
- Full media-player/PDF feature development.

## Guardrails

- Never hide raw/download when rendering fails.
- Never use extension alone to choose a privileged renderer.
- Never mutate a canonical dataset to “sanitize” it silently.
- Never allow artifact content to register components or handlers.
- Never bypass optimistic revision checks.

# PRD — Desktop Run Composer Parity

**Status:** Implemented (v1) · **Owner:** (PM) · **Surface:** `apps/desktop` Run cockpit · **Package:** `@0x-copilot/chat-surface`

> **v1 shipped** on `feat/desktop-composer-parity`: the desktop Run cockpit now
> mounts the shared `AssistantComposer` via a `renderComposer` seam on
> `TcChat`/`RunDestination`. Wired: model select (curated + local, `depthVisible=false`),
> MCP visibility (`+` menu) + non-MCP via the connectors trigger → Tools, `/`
> commands + skill pills, attachments (file picker + single-stage image/file
> adapter). **Deferred fast-follows:** the inline `/` autocomplete menu (FR-C2),
> the per-chat connector-scope popover (FR-D2 full), and the two-stage upload
> finalization (FR-B3). See §9 for the shipped checklist.

---

## 1. Context

`@0x-copilot/chat-surface` is the declared single-source-of-truth interaction
layer: web (`apps/frontend`) and desktop (`apps/desktop`) are supposed to mount
the _same_ composer and bind data through their own host adapters
(`packages/chat-surface/CLAUDE.md`).

In practice the **Run destination** ("ACTIVE RUN" cockpit) diverges:

|                             | Web (`apps/frontend`)                                               | Desktop (`apps/desktop`)                              |
| --------------------------- | ------------------------------------------------------------------- | ----------------------------------------------------- |
| `run` slug surface          | legacy `ChatScreen` → `ThreadBody`                                  | `RunDestination` → `ThreadCanvas` → `TcChat`          |
| Composer mounted            | package **`AssistantComposer`** (rich shell) — `ThreadBody.tsx:260` | package **base `Composer`** (bare) — `TcChat.tsx:296` |
| Attachments                 | ✅ `attachmentAdapter` + `filePicker` + `+`-menu                    | ❌ none                                               |
| `/` commands                | ✅ `/` opens skills pane + slash cue                                | ❌ none                                               |
| Connections (MCP + non-MCP) | ✅ `connectorsTrigger` (ConnectorPopover) + count                   | ❌ none                                               |
| Model selection             | ✅ `ModelPill`/`ModelPicker` (list + per-model reasoning)           | ❌ none                                               |
| Skill pills / mentions      | ✅                                                                  | ❌                                                    |

Both composers come from the _same package_; `AssistantComposer` merely **wraps**
the base `Composer` and injects the extra chrome via ports/slots
(`packages/chat-surface/src/composer/AssistantComposer.tsx`). The base `Composer`
is the inner primitive. Desktop stops at the primitive.

**Key enabling fact:** `AssistantComposer` is already framework-agnostic — every
substrate touchpoint (`FilePickerPort`, `renderPlusMenu` portal slot,
`connectorsTrigger` slot, `models`/`onModelChange`, `attachmentAdapter`) is
_injected_. Closing the gap is a **binding/wiring** task on desktop, not a rewrite.

---

## 2. Problem statements

- **P1 — Feature-poverty on desktop.** In the desktop Run cockpit the user cannot
  attach files/images, invoke `/` commands, see or scope which connections
  (MCP + non-MCP) are active, or pick a model. The desktop is the flagship
  distribution surface (fully-local + BYOK), so the _primary_ product experience
  is the _weakest_ composer.

- **P2 — SSOT contract is violated.** The stated architecture promises "no second
  copy… both apps mount the same composer." Two different composers ship under the
  same `run` slug. Fixes and features land in one surface and silently skip the
  other; the divergence compounds over time.

- **P3 — Model control is wrong-shaped.** Where a model control exists at all it is
  the Fast/Balanced/Deep _depth_ tri-toggle, not the model-list picker users expect
  from Cursor/Claude (searchable model list, per-model reasoning). Desktop needs the
  model-list picker; the depth grid is explicitly out of scope.

- **P4 — No connection transparency.** The user can't tell, from the composer, which
  connectors (MCP servers _and_ non-MCP integrations) are wired into the current
  run, nor scope them per-chat — a trust/observability gap for an agent that acts on
  external surfaces.

---

## 3. Goals / Non-goals

### Goals

- G1 — Desktop Run composer reaches **feature parity** with web's `AssistantComposer`:
  attachments, `/` commands, connection visibility (MCP + non-MCP), model selection.
- G2 — Achieve it by **mounting the shared `AssistantComposer`** in the Run cockpit
  (honor SSOT), not by forking a desktop composer.
- G3 — Model control is a **model-list picker with per-model reasoning** (Cursor/
  Claude shape), matching the reference screenshot.

### Non-goals

- NG1 — The **Fast / Balanced / Deep depth tri-toggle** is _not_ wanted
  (`depthVisible = false`). (User: "I don't want the deep balanced one.")
- NG2 — Migrating **web's** `run` slug onto `RunDestination` (the reciprocal SSOT
  cleanup) is out of scope for this effort — desktop-only ask. Tracked separately.
- NG3 — New composer capabilities that web does not already have, **except** the
  richer inline `/` menu called out in FR-C2 (explicitly flagged as enhancement).
- NG4 — Backend/model-routing changes. This is a client wiring effort against
  existing facade endpoints (`/v1/connectors`, `/v1/local-models`,
  `/v1/settings/provider-keys`, `/v1/agent/*`).

---

## 4. Users & primary scenarios

- **Solo desktop user (BYOK / local models)** — starts/continues an Active Run,
  drops in a file, `/`-invokes a skill, checks which connectors are live, switches
  model to one their key/local runtime supports — all from the run composer.

---

## 5. Functional requirements

### FR-A — Composer substrate (the load-bearing change)

- **FR-A1** The Run cockpit MUST render the shared `AssistantComposer` (from
  `@0x-copilot/chat-surface`) as its chat input, replacing the bare base `Composer`
  currently mounted by `TcChat`.
- **FR-A2** The swap MUST be inside the package (`TcChat`/`RunDestination` gains an
  injected composer slot, or mounts `AssistantComposer` directly) so **both** hosts
  benefit and no `apps/*→apps/*` import is introduced.
- **FR-A3** The desktop `RunBinder` (`apps/desktop/renderer/destinationBinders.tsx`)
  MUST supply the host-owned ports/slots the composer needs: `filePicker`,
  `renderPlusMenu`, `connectorsTrigger`, `attachmentAdapter`, model props, and the
  instruction-prompt builders — mirroring what web's `ThreadBody`/`ChatScreen` pass.
- **FR-A4** Existing Run-cockpit behavior MUST be preserved: scrub/off-live disables
  the composer (ghost state), Studio/Focus modes, single event projection (FR-3.3),
  no second SSE subscription.

### FR-B — Attachments

- **FR-B1** User can attach image(s) and file(s) via the `+` menu and see pending
  attachment pills before send.
- **FR-B2** Desktop MUST provide a `FilePickerPort` implementation (native dialog or
  renderer `<input type=file>`), analogous to web's `WebFilePickerPort`.
- **FR-B3** Attachments MUST flow through the runtime two-stage `attachmentAdapter`
  (`add` → `send` → `remove`); the host binds the bridge before handing it to the
  composer.
- **FR-B4** Attachment types/size/accept rules MUST match web (`fileAttachmentAccept`).

### FR-C — `/` commands

- **FR-C1 (parity, MVP)** Typing `/` on an empty composer MUST reproduce web
  behavior: reveal the slash cue and open the skills surface, and attaching a skill
  prefixes its instruction prompt on submit.
- **FR-C2 (enhancement, flagged)** A Cursor/Claude-style inline `/` menu (autocomplete
  list of skills/commands rendered at the caret, arrow-key select, Enter to insert)
  is desired beyond current web behavior. It MUST be built **in the package** so web
  inherits it too. Scoped as a fast-follow if it risks the parity milestone.

### FR-D — Connection visibility (MCP + non-MCP)

- **FR-D1** The composer MUST show a connections trigger reflecting the count of
  active connections for the current chat/run.
- **FR-D2** Opening it MUST list connectors — **both MCP servers and non-MCP
  integrations** — with per-chat enable/scope toggles, matching web's
  `ConnectorPopover`.
- **FR-D3** Data MUST come from the facade (`/v1/connectors`, per-chat connector
  scope); desktop already loads this in `ConnectorsBinder` and MUST reuse the same
  projection shape.
- **FR-D4** An empty/none state MUST route the user to connect a tool
  (Settings/Connectors), never a dead popover.

### FR-E — Model selection

- **FR-E1** The composer MUST render a **model-list picker** (`ModelPill` +
  `ModelPicker`): selectable list of available models with the active one indicated.
- **FR-E2** The list MUST be assembled from the user's real options — BYOK cloud
  models + registered custom (OpenRouter) slugs + running local models
  (`/v1/local-models`) — the same union web builds; models the user cannot run are
  shown disabled, not hidden.
- **FR-E3** Selecting a model MUST persist per-conversation and be sent on run/message
  dispatch (`provider` / `model_name` / `reasoning`), via `onModelChange`.
- **FR-E4** Per-model **reasoning** metadata MAY be surfaced (Cursor/Claude shape).
- **FR-E5** The Fast/Balanced/Deep **depth** control MUST be hidden
  (`depthVisible = false`). (NG1.)
- **FR-E6** Register-custom-model (`onAddCustomModel`, OpenRouter `vendor/model`)
  SHOULD be available, matching web.

### FR-F — Submit & run wiring

- **FR-F1** Submit MUST dispatch `{ text, attachments, model }` into the active
  run/conversation through the Transport port (identity derived from the verified
  session — never client-supplied).
- **FR-F2** While a run is in flight the Send affordance MUST become Stop
  (`running` → `onCancel`).
- **FR-F3** The empty-run `RunEmptyState` goal composer path MUST remain intact;
  starting a goal still binds the fresh run via the `runId` seam without remounting.

### FR-G — Keyboard & interaction parity

- **FR-G1** Enter = send, Shift+Enter = newline; imperative handle
  (`setText`/`appendText`/`addAttachment`/`submit`) preserved.
- **FR-G2** All controls reachable and operable by keyboard; the `+`-menu and
  popovers dismiss on outside-click / Escape.

---

## 6. Non-functional requirements

- **NFR-1 — Substrate boundary.** No new `apps/*→apps/*` import. The composer stays
  in the package; substrate specifics go through ports/slots. ESLint
  `no-restricted-globals` / `no-restricted-imports` MUST stay clean (no bare
  `window`/`document`/`fetch` in the package).
- **NFR-2 — Single source of truth.** One composer component serves both hosts after
  this change; no desktop-only composer fork is created.
- **NFR-3 — One event projection.** The cockpit continues to read exactly one event
  source (FR-3.3): no second SSE subscription or projector introduced by the wiring.
- **NFR-4 — Security / trust.** Attachments and model/connector selections carry no
  caller-asserted identity; the facade is the only egress; secrets (BYOK keys) never
  reach the composer. Connector scope changes are per-chat and auditable.
- **NFR-5 — Performance.** Composer mount + popovers add no perceptible input latency;
  connector/model lists load lazily and degrade to loading/empty/error states
  (no blocking the run view).
- **NFR-6 — Accessibility.** WCAG-AA: focus management for popovers/menus, ARIA roles
  on triggers and lists, visible focus, reduced-motion honored.
- **NFR-7 — Parity is testable.** A shared contract/prop-parity check asserts the Run
  composer exposes the same capability surface on both hosts; both host binders are
  updated together when composer props change (per `chat-surface/CLAUDE.md`).
- **NFR-8 — Graceful degradation.** Any unavailable capability (e.g. local models off,
  no connectors) degrades to a disabled/empty affordance, never a crash or blank pane.
- **NFR-9 — Theming.** Design-system tokens only; renders correctly in the desktop
  "quiet" v2 token set, light and dark.

---

## 7. Approach & recommendation

Two ways to satisfy the ask; recommendation first.

- **Option 1 — Mount `AssistantComposer` in the Run cockpit (RECOMMENDED).**
  Change `TcChat`/`RunDestination` to render the shared `AssistantComposer` (behind a
  composer slot), and have desktop's `RunBinder` supply the ports/slots. One package
  change + one binder change. Honors SSOT, benefits web too, no routing churn.
  _Cost:_ wiring the desktop `FilePickerPort`, `renderPlusMenu` portal, connectors
  trigger, and model catalog union.

- **Option 2 — Converge web's `run` onto `RunDestination` as well.**
  The full SSOT closure (both hosts run the same cockpit). Larger blast radius; web
  `ChatScreen` currently owns thread history + many flows. Out of scope here (NG2) but
  the natural follow-up once Option 1 proves the shared composer in-cockpit.

**Recommendation:** Ship **Option 1** for this ask. It directly delivers the parity
the user wants on desktop, respects the architecture boundary, and is the prerequisite
step for Option 2 later.

---

## 8. Open questions

- OQ1 — Does the Run cockpit's message/run dispatch accept the model selection
  fields today, or is a facade contract tweak needed for per-run model on desktop?
- OQ2 — `renderPlusMenu` needs a portal + outside-click owner on desktop; reuse the
  web `AnchoredPlusMenu` pattern via a desktop equivalent, or lift a shared
  portal helper behind a port?
- OQ3 — FR-C2 (inline `/` menu) — commit to MVP or fast-follow?
- OQ4 — Model-list source of truth: is `demoModels` (curated) the intended long-term
  catalog, or should desktop pull a real `/v1/...` model catalog?

---

## 9. Acceptance criteria (Definition of Done)

- [x] Desktop Run composer renders the shared `AssistantComposer` (not base `Composer`).
- [x] Attach image + file renders pills; single-stage adapter emits the run content
      shape (`{type:"image",image}` / `{type:"file",…}`). _(Two-stage upload finalize deferred — FR-B3.)_
- [x] `/` on empty composer opens skills + slash cue (parity). _(Inline `/` autocomplete deferred — FR-C2.)_
- [x] Connections trigger shows active count → opens Tools (MCP + non-MCP); MCP servers
      listed in the `+` menu. _(Per-chat scope popover deferred — FR-D2 full.)_
- [x] Model-list picker present; list = curated cloud (gated on BYOK keys) + custom
      (OpenRouter) + local; selection persists and is sent on dispatch; **no**
      Fast/Balanced/Deep depth control visible (`depthVisible={false}`).
- [x] Scrub/off-live still disables the composer; Studio/Focus modes intact;
      single event projection preserved. _(Pinned by TcChat seam tests.)_
- [x] No `apps/*→apps/*` import; package ESLint boundary clean; desktop binder updated.
- [x] Web Run composer unchanged in behavior (additive optional props only; 426
      chat-surface tests + 84 desktop renderer tests green).
- [ ] a11y sweep: keyboard-operable, popovers focus-trapped + Escape/outside-click
      dismiss. _(Portal + outside-click implemented; full a11y audit pending.)_
- [ ] Live-smoke on the supervised Electron boot (CLI harness) — recommended before ship.

---

### Evidence index (for engineering)

| Claim                                  | File                                                                   |
| -------------------------------------- | ---------------------------------------------------------------------- |
| Desktop run → base `Composer`          | `packages/chat-surface/src/thread-canvas/TcChat.tsx:9,296`             |
| Web run → `AssistantComposer`          | `apps/frontend/src/features/chat/components/thread/ThreadBody.tsx:260` |
| Web maps `run`→`ChatScreen`            | `apps/frontend/src/app/App.tsx` (`route.destination === "run"`)        |
| Desktop maps `run`→`RunDestination`    | `apps/desktop/renderer/DestinationOutlet.tsx:146`                      |
| Composer is port-injected (agnostic)   | `packages/chat-surface/src/composer/AssistantComposer.tsx` (props)     |
| Depth toggle is separable              | `AssistantComposer` `depthVisible` prop                                |
| Model union (curated+custom+local)     | `apps/frontend/src/features/chat/ChatScreen.tsx:289`                   |
| Desktop connectors data already loaded | `apps/desktop/renderer/destinationBinders.tsx:391` (`loadConnectors`)  |
| Web file picker port                   | `apps/frontend/src/app/App.tsx:497` (`WebFilePickerPort`)              |

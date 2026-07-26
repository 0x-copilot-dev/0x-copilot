# PRD-CT-FE — Unified Composer Tools

**Status:** proposed implementation plan
**Owners:** Chat-surface, Desktop, Frontend
**Companion:** [backend capability catalogue PRD](backend-prd.md)

## Problem

The existing Tools popover is an MCP-oriented view assembled from
`/v1/mcp/servers` and `/v1/mcp/catalog`, plus a local Web search toggle. The
legacy `ToolPicker` reads `/v1/mcp/tools`, but it is not the canonical Run or
first-run control. This gives users different tool lists depending on which
composer surface they enter, and it cannot faithfully represent all capabilities
shown in the supplied design.

The immediate layering defect is repaired in the shared composer: the visible
popover must also be its own hit target. This PRD specifies the follow-on product
work: one clear, policy-aware selector across desktop and web.

## Goals

- One shared Tools trigger and popover for first run, empty Run, active Run, and
  web chat; host code supplies transport, navigation, and desktop OAuth.
- Show all _eligible_ capabilities for the authenticated user/workspace:
  built-ins (including Web search), connected MCP tools, skills when they are
  invocable, and local/browser/file capabilities only when the runtime supports
  and permits them.
- Explain availability before send: active, paused, needs sign-in, needs setup,
  policy blocked, unavailable, or loading/error.
- Carry an explicit, accessible next-run selection to the run creation request.
- Keep the supplied v3 tool-call shell visually aligned without duplicating the
  design system's scrim/panel recipe in each host.

## Non-goals

- Do not expose a capability solely because a mock names it.
- Do not persist an unvalidated client-side permission grant.
- Do not implement tool execution, OAuth token storage, or approval enforcement
  in React.
- Do not make selecting a write tool silently authorize writes.

## User experience

| State                       | Trigger / popover behaviour                                                                      | User outcome                                                         |
| --------------------------- | ------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------- |
| Built-in Web search allowed | Web search is on by workspace default and can be paused for this run.                            | `web_search_enabled: false` is sent only for an explicit opt-out.    |
| Connected MCP capability    | It shows provider, capability summary, read/write classification, and an on/off next-run switch. | Enabled IDs become server-authorized run scopes.                     |
| OAuth needed                | Row is not an active switch; it says **Connect** and starts the host-owned OAuth path.           | Return/cancel/error is visible, then the catalogue refreshes.        |
| Vendor setup needed         | Row says **Set up** and routes to custom MCP configuration.                                      | No blind install request that would fail with 422.                   |
| Policy blocked              | Row is disabled with a concise reason and optional admin route.                                  | It cannot be sent in the selection.                                  |
| Write/approval capable      | Row carries an `acts` indicator and an approval note.                                            | Selection remains possible; each mutation still pauses for approval. |
| Loading/error/empty         | Stable panel chrome with status copy and retry where applicable.                                 | The composer remains usable and no stale selection is invented.      |

### Interaction and accessibility contract

- The trigger is a labelled button with `aria-haspopup="dialog"` and truthful
  `aria-expanded`; its count is the enabled next-run count.
- Opening moves focus into the `role="dialog"` panel. Escape, close, and a
  pointer-down outside close it. Pointer interaction inside must target the row,
  not the transparent scrim.
- Tab order is trigger → controls in visual order → close. Every switch has a
  visible label, keyboard toggle semantics, and `aria-checked`.
- Use the shared `.ui-pop-scrim` / `.ui-pop` contract (70/71) and the
  `aui-composer-tools--tools-popover` host layer. Do not add global document
  listeners or a second menu dismiss primitive.
- Support reduced motion, 320px-wide layouts, keyboard-only operation, high
  contrast tokens, and long provider/tool names without hiding the control.

## Target client contract

Add a host-injected `ComposerToolsPort` in `packages/chat-surface`; hosts must
not independently join MCP servers, MCP catalogue, skills, and built-ins.

```ts
type ComposerToolAvailability =
  | "enabled"
  | "disabled"
  | "needs_auth"
  | "needs_setup"
  | "policy_blocked"
  | "unavailable";

interface ComposerToolDescriptor {
  id: string; // opaque, server-issued reference
  label: string;
  description?: string;
  kind: "builtin" | "mcp" | "skill" | "local";
  availability: ComposerToolAvailability;
  defaultEnabled: boolean;
  permission: "read" | "write" | "mixed";
  provider?: { name: string; iconKey?: string };
  remediation?: "connect" | "configure" | "request_access";
  disabledReason?: string;
}

interface ComposerToolsPort {
  list(): Promise<{
    tools: readonly ComposerToolDescriptor[];
    revision: string;
  }>;
}
```

The UI keeps a draft selection keyed by descriptor ID. It emits only
server-approved selection primitives in `RunStartRequest` (the exact
`tool_selection` wire shape is owned by the backend PRD). Existing
`webSearchEnabled` and `connectorScopes` remain adapters during migration; they
must not become a second source of truth.

## Delivery plan

1. **Stabilize current surfaces (now):** preserve the repaired inline popover,
   add the desktop hit-target journey, and keep current Web search/MCP behaviour.
2. **Shared catalogue seam:** add the port, descriptor projection, loading/error
   states, and a feature flag that adapts existing endpoint responses.
3. **Unified picker:** replace duplicated `FirstRunToolsTrigger`,
   `ChatToolsTrigger`, and desktop trigger bodies with one shared renderable
   control. Desktop provides OAuth/browser routing; web provides browser routing.
4. **Rich policy UX:** add availability, permission, approval, and remediation
   UI from the authoritative descriptor. Remove legacy `ToolPicker` only after
   all consumers use the new port.
5. **Parity and rollout:** enable per workspace, instrument open/select/connect/
   blocked events without prompt contents, then remove the compatibility join.

## Acceptance criteria

- No supported composer renders a dead Tools button or a panel behind its scrim.
- The same eligible catalogue and selection semantics appear in desktop and web
  for the same signed-in workspace.
- A Web search opt-out results in `web_search_enabled: false`; default-on does
  not add a contradictory payload.
- A connected selected MCP tool reaches the run request only through an
  authenticated, server-known connector scope.
- Needs-auth/setup/policy-blocked rows cannot be mistaken for enabled tools.
- User journeys in `tools/desktop-journeys/composer-tools/JOURNEYS.md` pass,
  plus unit coverage for panel state and host binding coverage for each surface.
- The tool-call parity harness reports no high/medium regressions for its six
  fixture states, and the interactive journey proves pointer hit testing.

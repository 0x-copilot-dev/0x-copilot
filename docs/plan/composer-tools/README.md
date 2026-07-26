# Composer Tools programme

## Purpose

The composer Tools control is the user-facing selector for capabilities that may
participate in the next run. It must make the difference between a built-in tool,
an authenticated connector, a skill, and a capability requiring approval clear
before the user sends a message.

This programme is grounded in these local design references:

- `tools/design-parity/surfaces/chat-tool-call-shell/design/reference.dc.html`
  (vendored parity reference)
- `/Users/parthpahwa/Downloads/copilot-project-folder-copy/project/Chat & Tool Calls.dc.html`
  (the supplied latest design)
- `tools/design-parity/surfaces/chat-tool-calls/design/index.html` (tool-call
  fixture states)

The supplied design demonstrates Web search, third-party read tools, local file
read/write, and browser-like research. It is a product inventory, not proof that
every entry is safely runnable today. A tool is selectable only after the backend
can resolve it for the authenticated workspace and apply its policy.

## Reference capability inventory

The walkthrough's scripted run makes the intended taxonomy explicit:

| Capability path                   | Class                  | Expected policy/outcome in the design                                               |
| --------------------------------- | ---------------------- | ----------------------------------------------------------------------------------- |
| `linear.issues.get`               | Third-party MCP read   | OAuth-backed read; the result can render a known record surface.                    |
| `web.search` → `web.fetch` / rank | Built-in research/read | No auth; sources and citations stay in chat rather than inventing a record surface. |
| `fs.read` (`forecast_q1.csv`)     | Local read             | Sandboxed local analysis; CSV statistics and prose remain inline.                   |
| `fs.write` (`standup-…md`)        | Local write            | Held for an explicit approval, then shown as a file-action card.                    |

The design label “6 tools live” is a demo aggregate, not a stable list of six
safe menu entries. The production selector must calculate both availability and
the active count from the server-authoritative catalogue.

## Documents

- [Frontend PRD](frontend-prd.md) — shared composer UI, accessibility, host
  bindings, and visual acceptance.
- [Backend PRD](backend-prd.md) — the server-authoritative capability catalogue,
  run snapshot, policy, auth, and audit contracts.
- [Desktop journey matrix](../../../tools/desktop-journeys/composer-tools/JOURNEYS.md)
  — executable and manual end-to-end coverage.

## Current repair

The current inline panel could mount visibly while its transparent click-out
scrim intercepted every pointer event. The repair lets the Tools control group
escape clipping and establishes one host overlay layer above the shared
scrim/panel pair. It applies to the desktop Run, empty Run, first-run composer,
and web chat shared composer.

This is deliberately separate from the catalogue programme: it restores an
existing interaction without claiming that the current MCP-only picker is a
complete capability registry.

## Decisions captured here

1. The active count is the number of enabled tools for the next run, not the
   total number in a catalogue. The popover may separately show the eligible
   catalogue count.
2. The client requests choices; the backend resolves and validates the final
   executable capability set. It must never trust a client-supplied server URL,
   permission level, or identity.
3. A missing OAuth grant, a pre-registered-client requirement, or a denied
   policy is a clear state with a safe route to remediation — never a fake
   enabled toggle.
4. Write-capable tools require a runtime approval boundary even if they are
   selected in the composer. Selection is not authorization to mutate data.

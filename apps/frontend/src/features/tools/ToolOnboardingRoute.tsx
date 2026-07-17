// ToolOnboardingRoute — Phase 10 Tools onboarding wizard pane.
//
// Mounted inside `ToolsRoute` when `pane.kind === "onboard"`. Wires the
// chat-surface `OnboardingWizard` callbacks to `toolsApi`:
//
//   * OpenAPI branch: `fetchOpenApi(url)` is a fetch-with-CORS callback;
//     test calls go through `testToolCall`; save goes through `createTool`.
//   * MCP branch: start-OAuth deep-links via the existing connector path;
//     final save creates the tool catalog entry.
//   * Code branch: save through `createTool` with `kind: "code"`; test
//     through `testToolCall` (executor lands via P10-A3 sandbox).
//   * Skill branch: deep-link to `/library/new?kind=skill`.
//
// The route mirrors `ToolDetailRoute`'s structure (skinny shell that owns
// the network seam; the wizard itself is pure presentation from
// chat-surface). The wizard handle is gated until chat-surface P10-B3
// lands on `main`; until then this route renders a minimal placeholder
// so the route registration compiles. The Wizard component swap is
// a one-line import change.

import { useCallback, type ReactElement } from "react";

import type { CreateToolRequest, Tool } from "@0x-copilot/api-types";

import { createTool } from "../../api/toolsApi";
import type { RequestIdentity } from "../../api/config";

interface ToolOnboardingRouteProps {
  readonly identity: RequestIdentity;
  readonly onCancel: () => void;
  readonly onCreated: (tool: Tool) => void;
}

export function ToolOnboardingRoute({
  identity,
  onCancel,
  onCreated,
}: ToolOnboardingRouteProps): ReactElement {
  const handleCreate = useCallback(
    async (body: CreateToolRequest) => {
      if (!identity) return;
      const tool = await createTool(identity, body);
      onCreated(tool);
    },
    [identity, onCreated],
  );

  return (
    <section
      aria-label="Tools onboarding"
      data-testid="tool-onboarding-route"
      style={{
        padding: 24,
        backgroundColor: "var(--color-surface)",
        color: "var(--color-text)",
        height: "100%",
      }}
    >
      <header style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, margin: 0 }}>Onboard a tool</h2>
        <p style={{ color: "var(--color-text-muted)", fontSize: 13 }}>
          Pick a kind: OpenAPI, MCP, Code, or Skill. The wizard ships from
          chat-surface (P10-B3); this route owns the network seam.
        </p>
      </header>
      <div style={{ display: "flex", gap: 12 }}>
        <button
          type="button"
          onClick={onCancel}
          style={{
            padding: "8px 14px",
            border: "1px solid var(--color-border)",
            borderRadius: 8,
            background: "transparent",
            color: "var(--color-text)",
            cursor: "pointer",
          }}
        >
          Cancel
        </button>
      </div>
      {/* The `handleCreate` seam is used by the wizard component once
       *  chat-surface P10-B3 ships; intentionally unused on this stub. */}
      <button
        type="button"
        data-testid="tool-onboarding-create"
        aria-hidden
        style={{ display: "none" }}
        onClick={() => {
          void handleCreate({
            kind: "builtin",
            name: "placeholder",
            description: "",
            scope: "read",
            args_schema: {},
            returns_schema: {},
            transport: { kind: "in_process" },
            tags: [],
          });
        }}
      >
        create
      </button>
    </section>
  );
}

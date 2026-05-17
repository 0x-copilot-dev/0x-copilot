// Single source of truth for: destination slug, human label, sidebar icon
// hint (keyword the AppRail maps to its own icon set), and the chat-surface
// component rendered for each ArtifactRoute kind. Phase 1 components are
// stubs; Phase 3 destination agents replace them.

import { createElement, type ComponentType } from "react";

import type { ArtifactRoute } from "./router";
import { ARTIFACT_SCHEMES, type ArtifactScheme } from "./uri/schemes";

export interface RouteEntry {
  readonly kind: ArtifactRoute["kind"];
  readonly scheme: ArtifactScheme;
  readonly label: string;
  readonly iconHint: string;
  readonly Component: ComponentType<{ readonly route: ArtifactRoute }>;
}

function stub(label: string): ComponentType<{ readonly route: ArtifactRoute }> {
  const Stub = ({ route }: { readonly route: ArtifactRoute }) =>
    createElement("div", { "data-testid": `route-stub-${route.kind}` }, label);
  Stub.displayName = `RouteStub(${label})`;
  return Stub;
}

export const ROUTE_TABLE: Readonly<Record<ArtifactRoute["kind"], RouteEntry>> =
  {
    chat: {
      kind: "chat",
      scheme: ARTIFACT_SCHEMES.chat,
      label: "Chat",
      iconHint: "message-square",
      Component: stub("Chat"),
    },
    conversation: {
      kind: "conversation",
      scheme: ARTIFACT_SCHEMES.conversation,
      label: "Conversation",
      iconHint: "messages-square",
      Component: stub("Conversation"),
    },
    run: {
      kind: "run",
      scheme: ARTIFACT_SCHEMES.run,
      label: "Run",
      iconHint: "play",
      Component: stub("Run"),
    },
    subagent: {
      kind: "subagent",
      scheme: ARTIFACT_SCHEMES.subagent,
      label: "Subagent",
      iconHint: "bot",
      Component: stub("Subagent"),
    },
    "tool-result": {
      kind: "tool-result",
      scheme: ARTIFACT_SCHEMES.toolResult,
      label: "Tool result",
      iconHint: "wrench",
      Component: stub("Tool result"),
    },
    mcp: {
      kind: "mcp",
      scheme: ARTIFACT_SCHEMES.mcp,
      label: "Connector",
      iconHint: "plug",
      Component: stub("Connector"),
    },
    "mcp-tool": {
      kind: "mcp-tool",
      scheme: ARTIFACT_SCHEMES.mcpTool,
      label: "Tool",
      iconHint: "tool",
      Component: stub("Tool"),
    },
    skill: {
      kind: "skill",
      scheme: ARTIFACT_SCHEMES.skill,
      label: "Skill",
      iconHint: "sparkles",
      Component: stub("Skill"),
    },
    workspace: {
      kind: "workspace",
      scheme: ARTIFACT_SCHEMES.workspace,
      label: "Workspace",
      iconHint: "building",
      Component: stub("Workspace"),
    },
  };

/**
 * Default route on app open. `null` means "show the empty-state landing
 * pad" — the AppRail / shell decides what to render until the user
 * navigates. Phase 1-D deliberately does NOT synthesize a default
 * ArtifactRoute, because the Atlas product's true default destination is
 * "home", which has no corresponding ArtifactRoute kind today.
 */
export const DEFAULT_ROUTE: ArtifactRoute | null = null;

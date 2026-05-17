export const ARTIFACT_SCHEMES = {
  chat: "chat",
  conversation: "convo",
  run: "run",
  subagent: "subagent",
  toolResult: "tool-result",
  email: "email",
  sheetRow: "sheet-row",
  sfOpportunity: "sf-opp",
  slide: "slide",
  mcp: "mcp",
  mcpTool: "mcp-tool",
  skill: "skill",
  workspace: "workspace",
  timeMachine: "time-machine",
} as const;

export type ArtifactScheme =
  (typeof ARTIFACT_SCHEMES)[keyof typeof ARTIFACT_SCHEMES];

const SCHEME_VALUES: ReadonlySet<string> = new Set(
  Object.values(ARTIFACT_SCHEMES),
);

export function isArtifactScheme(value: string): value is ArtifactScheme {
  return SCHEME_VALUES.has(value);
}

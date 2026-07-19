// Instruction-prompt builders injected into the shared `AssistantComposer`.
//
// The composer prefixes a selected skill's instruction on submit and inserts a
// "use MCP server" instruction from the `+` menu. The strings live host-side
// (the package takes them as props) so the substrate-agnostic core never
// imports a host `prompts` module. Kept byte-identical to the web builders
// (apps/frontend/src/features/chat/prompts) — the two hosts intentionally
// duplicate this pure copy because `apps/* → apps/*` imports are banned.

export function skillInstructionPrompt(displayName: string): string {
  return `Use the ${displayName} skill for this request.`;
}

export function mcpServerInstructionPrompt(displayName: string): string {
  return `Use the ${displayName} MCP server for this request.`;
}

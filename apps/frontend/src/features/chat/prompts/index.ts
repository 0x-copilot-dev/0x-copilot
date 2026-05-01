export type ChatPromptSuggestion = {
  title: string;
  label: string;
  prompt: string;
};

export const CHAT_PROMPT_SUGGESTIONS: ChatPromptSuggestion[] = [
  {
    title: "Search connectors",
    label: "Find context across connected apps",
    prompt:
      "Search connected apps for relevant context and summarize the findings.",
  },
  {
    title: "Think through risks",
    label: "Show reasoning and tool usage",
    prompt:
      "Think through the main risks, use available tools, and explain the recommendation.",
  },
  {
    title: "Call a subagent",
    label: "Delegate research",
    prompt:
      "Call a research subagent to investigate this and report back with sources.",
  },
];

export const REGENERATE_PREVIOUS_RESPONSE_PROMPT =
  "Regenerate the previous response.";

export function mcpServerInstructionPrompt(displayName: string): string {
  return `Use the ${displayName} MCP server for this request.`;
}

export function skillInstructionPrompt(displayName: string): string {
  return `Use the ${displayName} skill for this request.`;
}

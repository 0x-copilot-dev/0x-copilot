export type ChatPromptCategory = "draft" | "summarize" | "find" | "compare";

/**
 * The shape consumed by both the welcome state and the assistant-ui runtime
 * (`Suggestions(CHAT_PROMPT_SUGGESTIONS)`). The runtime's `SuggestionConfig`
 * accepts `{title, label, prompt}`; the `category` field is an extra used
 * only by the welcome-state UI to render an intent eyebrow. Width subtyping
 * keeps `ChatPromptSuggestion[]` assignable to `SuggestionConfig[]`.
 */
export type ChatPromptSuggestion = {
  category: ChatPromptCategory;
  title: string;
  label: string;
  prompt: string;
};

export const CATEGORY_LABEL: Record<ChatPromptCategory, string> = {
  draft: "DRAFT",
  summarize: "SUMMARIZE",
  find: "FIND",
  compare: "COMPARE",
};

export const CHAT_PROMPT_SUGGESTIONS: ChatPromptSuggestion[] = [
  {
    category: "draft",
    title: "Draft the FY26 Q1 launch announcement",
    label: "Using the approved positioning + GTM plan",
    prompt:
      "Draft the FY26 Q1 launch announcement using the approved positioning + GTM plan. Pull citations and propose a Slack post for review.",
  },
  {
    category: "summarize",
    title: "Summarize last week in #launch-aurora",
    label: "Decisions, blockers, and who owns what",
    prompt:
      "Summarize last week in #launch-aurora. Group by Decisions, Blockers, and Owners.",
  },
  {
    category: "find",
    title: "Find the latest brand voice guidelines",
    label: "And tell me what changed since Q3",
    prompt:
      "Find the latest brand voice guidelines and tell me what changed since Q3.",
  },
  {
    category: "compare",
    title: "Compare our positioning vs Glean",
    label: "From the competitive frame doc",
    prompt:
      "Compare our positioning vs Glean using the competitive frame doc. Cite sources.",
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

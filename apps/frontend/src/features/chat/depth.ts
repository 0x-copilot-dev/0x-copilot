// Re-export shim for the thinking-depth domain.
//
// The depth model (`ThinkingDepth` + its label/description/support helpers)
// now lives in @0x-copilot/chat-surface (PR-1.2), moved down with the
// `ThinkingDepthControl` it drives so web and desktop share one source of
// truth. It has no substrate-specific dependency, so this is a pure
// re-export; existing import sites (`ChatScreen`, `chatDepthKv`, `Topbar`,
// `ThreadBody`, `depth.test.ts`) keep resolving these symbols from here.
//
// FR-1.7 flag: this `ThinkingDepth` model still coexists with the base
// chat-surface `Depth` / `listDepthDescriptors`; reconciliation is deferred
// to Phase 3E (see packages/chat-surface/src/composer/depth.ts).

export {
  THINKING_DEPTHS,
  DEFAULT_THINKING_DEPTH,
  isThinkingDepth,
  depthLabel,
  depthLabelForModel,
  depthDescription,
  modelSupportsDepth,
  type ThinkingDepth,
} from "@0x-copilot/chat-surface";

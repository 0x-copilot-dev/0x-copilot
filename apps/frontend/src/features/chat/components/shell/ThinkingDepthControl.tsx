// Re-export shim for the thinking-depth radiogroup.
//
// The component now lives in @0x-copilot/chat-surface (PR-1.2) so web and
// desktop render the Fast / Balanced / Deep control identically. It takes
// its value + callbacks via props and has no substrate-specific
// dependency, so this is a pure re-export rather than a host adapter;
// existing import sites (and the `shell` barrel) keep resolving
// `ThinkingDepthControl` from here.

export {
  ThinkingDepthControl,
  type ThinkingDepthControlProps,
} from "@0x-copilot/chat-surface";

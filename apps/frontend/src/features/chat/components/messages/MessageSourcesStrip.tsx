// Re-export shim for the post-prose Sources strip.
//
// The component now lives in @0x-copilot/chat-surface (PR-1.4) so web and
// desktop render the strip identically. It has no substrate-specific
// dependency, so this is a pure re-export rather than a host adapter;
// existing import sites keep resolving `MessageSourcesStrip` from here.

export {
  MessageSourcesStrip,
  type MessageSourcesStripProps,
} from "@0x-copilot/chat-surface";

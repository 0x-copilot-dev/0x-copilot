// Re-export shim for the inset key/value params frame.
//
// The component now lives in @0x-copilot/chat-surface (PR-1.6, moved with
// the approval family). Pure presentation, no substrate dependency;
// existing host import sites (`ActivityCard`) keep resolving `ActivityParams`
// from here.

export { ActivityParams } from "@0x-copilot/chat-surface";

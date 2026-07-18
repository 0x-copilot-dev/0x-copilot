// Re-export shim for the elapsed-seconds hook.
//
// The hook now lives in @0x-copilot/chat-surface (PR-1.5). Its interval was
// rewritten from `window.setInterval` to the bare `setInterval` global
// (FR-1.30) so it is substrate-portable while keeping the 5000 ms cadence
// byte-identical. Existing import sites keep resolving `useElapsedSeconds`
// from here.

export { useElapsedSeconds } from "@0x-copilot/chat-surface";

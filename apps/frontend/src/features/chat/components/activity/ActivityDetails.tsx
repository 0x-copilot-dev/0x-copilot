// Re-export shim for the approval tool-details disclosure.
//
// The component now lives in @0x-copilot/chat-surface (PR-1.6, moved with
// the approval family that renders it). The moved core inlines the same
// `<details>` DOM the host `ActivityCollapsible` produces, so the markup is
// byte-identical; existing host import sites (`ActivityCard`) keep resolving
// `ActivityDetails` from here.

export { ActivityDetails } from "@0x-copilot/chat-surface";

// Re-export shim for the activity status icon (spinner / ✓ / !).
//
// The component now lives in @0x-copilot/chat-surface (PR-1.5) as a shared
// leaf of the subagent card family. It maps a status string to an icon via
// the same `statusClassification` table; existing import sites (incl.
// `ActivityItem`) keep resolving `ActivityStatusIcon` from here.

export { ActivityStatusIcon } from "@0x-copilot/chat-surface";

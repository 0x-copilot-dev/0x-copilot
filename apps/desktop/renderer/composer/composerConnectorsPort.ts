// Desktop composer connectors port — the MCP connector surface for the Run
// cockpit composers' inline Tools popover.
//
// This is the SAME `/v1/mcp/*` facade adapter the first-run Tools popover uses
// (`createFirstRunConnectorsPort`); the run composers reuse it verbatim behind a
// neutral (non-FTUE) name. chat-surface exports the port type under the neutral
// alias `ComposerConnectorsPort` (= `FirstRunConnectorsPort`), so hosts wiring
// the chat/run Tools popover never reach for a "first-run" symbol.
//
// Keeping this as a thin re-export (rather than a second implementation) means
// there is one desktop implementation of the port — a connector connected in the
// FTUE, in chat, or in the run cockpit all speak to the same routes.

export { createFirstRunConnectorsPort as createComposerConnectorsPort } from "../onboarding/firstRunConnectorsPort";

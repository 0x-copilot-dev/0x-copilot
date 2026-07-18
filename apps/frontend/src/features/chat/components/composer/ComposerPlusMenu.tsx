// Re-export shim for the composer `+` menu (root / MCP / skills views).
//
// The component now lives in @0x-copilot/chat-surface (PR-1.2) so web and
// desktop render the attachment/tools menu identically. It is a pure
// presentational switch driven by props (view + callbacks) with no
// substrate-specific dependency, so this is a pure re-export rather than a
// host adapter; existing import sites keep resolving `ComposerPlusMenu`
// (and `ComposerMenuView`) from here.

export {
  ComposerPlusMenu,
  type ComposerMenuView,
} from "@0x-copilot/chat-surface";

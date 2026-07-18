// Re-export shim for the composer-anchored connectors trigger.
//
// The component now lives in @0x-copilot/chat-surface (PR-1.2) so web and
// desktop render the connectors button identically. It takes its
// activeCount + open state + onClick via props (the host mounts the shared
// ConnectorPopover) and has no substrate-specific dependency, so this is a
// pure re-export rather than a host adapter; existing import sites keep
// resolving `ComposerConnectorsButton` from here.

export {
  ComposerConnectorsButton,
  type ComposerConnectorsButtonProps,
} from "@0x-copilot/chat-surface";

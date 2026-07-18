import type { SafeStorageLike } from "../auth/secret-storage";

import { CapabilityBroker } from "./broker";
import { FolderPicker, type ShowOpenDialogResult } from "./folder-picker";
import { GrantStore, type GrantStoreAudit } from "./grant-store";
import { CapabilityService } from "./service";

// Composition root for the capability subsystem (AC5 slice 1). Kept
// electron-free (deps injected) so it is unit-testable; main/index.ts binds
// `showOpenDialog` to Electron's `dialog` + the main window and passes the
// real `safeStorage`.

export interface CreateCapabilityServiceConfig {
  readonly userDataDir: string;
  readonly safeStorage: SafeStorageLike;
  /** Main binds this to `dialog.showOpenDialog({ properties:['openDirectory'] })`. */
  readonly showOpenDialog: () => Promise<ShowOpenDialogResult>;
  /** Dev-only plaintext fallback for the grant store when no OS keychain. */
  readonly allowPlaintextFallback?: boolean;
  readonly audit?: GrantStoreAudit;
}

export function createCapabilityService(
  config: CreateCapabilityServiceConfig,
): CapabilityService {
  const store = new GrantStore({
    userDataDir: config.userDataDir,
    safeStorage: config.safeStorage,
    allowPlaintextFallback: config.allowPlaintextFallback,
    audit: config.audit,
  });
  const picker = new FolderPicker({ showOpenDialog: config.showOpenDialog });
  const broker = new CapabilityBroker({ grants: store });
  return new CapabilityService({ store, picker, broker });
}

export {
  CAPABILITY_CHANNELS,
  CAPABILITY_CHANNEL_VALUES,
  isCapabilityChannel,
  type CapabilityChannelName,
} from "./channels";
export { CapabilityService } from "./service";
export { CapabilityBroker, CAPABILITY_BROKER_PROTOCOL } from "./broker";
export { GrantStore } from "./grant-store";
export {
  FolderPicker,
  FolderPickerError,
  sanitizeLabel,
} from "./folder-picker";
export {
  GrantModeSchema,
  ListGrantsParamsSchema,
  RendererGrantSchema,
  RequestFolderGrantParamsSchema,
  RevokeGrantParamsSchema,
  type RequestFolderGrantParams,
  type RevokeGrantParams,
} from "./schemas";
export {
  toRendererGrant,
  type Grant,
  type GrantMode,
  type GrantProvider,
  type GrantSnapshot,
  type GrantStatus,
  type RendererGrant,
} from "./types";

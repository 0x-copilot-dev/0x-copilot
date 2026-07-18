import type { SafeStorageLike } from "../auth/secret-storage";

import { CapabilityBroker } from "./broker";
import { FolderPicker, type ShowOpenDialogResult } from "./folder-picker";
import { GrantStore, type GrantStoreAudit } from "./grant-store";
import { HostFs } from "./host-fs";
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
  // The broker's FS routes execute reads through HostFs; without it they fail
  // closed. HostFs itself is stateless — it only ever touches paths under a
  // grant root the broker resolves from the store.
  const broker = new CapabilityBroker({ grants: store, hostFs: new HostFs() });
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
  RunContextStore,
  type RunCapabilityContext,
  type RunContextStoreConfig,
} from "./run-context";
export {
  HostFs,
  defaultHostFsDeps,
  type HostFsDeps,
  type ReadOptions,
  type GlobOptions,
  type GrepOptions,
} from "./host-fs";
export {
  FsError,
  FS_LIMITS,
  normalizeVirtualPath,
  assertWithinRoot,
  modeSatisfies,
  assertGrantableRoot,
  classifyForbiddenRoot,
  isSensitiveFileName,
  SENSITIVE_ROOT_SEGMENTS,
  SENSITIVE_FILE_RULES,
  type FsErrorCode,
  type ForbiddenRootReason,
  type GrantRootContext,
} from "./path-validation";
export {
  DESKTOP_FILESYSTEM_FLAG,
  isDesktopFilesystemEnabled,
} from "./feature-gate";
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
  toBrokerGrant,
  type Grant,
  type GrantMode,
  type GrantProvider,
  type GrantSnapshot,
  type GrantStatus,
  type RendererGrant,
  type BrokerGrant,
  type BrokerGrantSnapshot,
  type HostEntryType,
  type HostStatResult,
  type HostDirEntry,
  type HostListResult,
  type HostReadResult,
  type HostGlobResult,
  type HostGrepHit,
  type HostGrepResult,
  type HostWriteResult,
  type HostEditResult,
  type HostMkdirResult,
  type HostDeleteResult,
  type HostMoveResult,
} from "./types";

import type { SafeStorageLike } from "../auth/secret-storage";
import type { LocalServiceChannelCredential } from "../services/local-service-identity";

import { CapabilityBroker, type McpSecretStore } from "./broker";
import { FolderPicker, type ShowOpenDialogResult } from "./folder-picker";
import { GrantStore, type GrantStoreAudit } from "./grant-store";
import { HostFs } from "./host-fs";
import { UnavailableNativeWorkspaceAuthority } from "./native-workspace-authority";
import { CapabilityService } from "./service";
import {
  InMemoryWorkspaceJournalStore,
  LocalWorkspaceAuthority,
  type NativeWorkspaceAuthority,
  type WorkspaceWriteAttestation,
} from "./workspace-authority";
import { EncryptedWorkspaceJournalStore } from "./workspace-journal";

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
  /**
   * Main-only writable-workspace bootstrap. Omitting it deliberately installs
   * an unavailable authority: reads retain their existing review path, while
   * every legacy direct mutation route is disabled.
   *
   * A future host bootstrap must supply facts derived from the verified local
   * session/device plus a persistent main-owned journal key. Renderer or
   * AI-backend input must never populate these fields.
   */
  readonly workspace?: WorkspaceAuthorityBootstrap;
  /** Named children permitted to call the main-owned local broker. */
  readonly localBrokerClients?: readonly LocalServiceChannelCredential[];
  /**
   * Main-owned MCP connector credential source for the broker's `/mcp/secret`
   * route (`SecretStorage`, whose ACTIVE-WORKSPACE gate this relies on).
   * Omitting it leaves the route failing closed and unadvertised — direct
   * MCP connect is then simply unavailable, which is the safe default.
   */
  readonly mcpSecrets?: McpSecretStore;
}

export interface WorkspaceAuthorityBootstrap {
  readonly native: NativeWorkspaceAuthority;
  readonly attestation: WorkspaceWriteAttestation;
  readonly production: boolean;
  readonly profileId?: string;
  /** Main-only late resolver for the verified signed-in profile. */
  readonly profileIdResolver?: () => Promise<string | null>;
  readonly deviceId: string;
  /** Stable per-installation secret; required for restart-safe recovery. */
  readonly journalIntegrityKey: Buffer;
  /** Explicit development-only journal fallback; never enable in production. */
  readonly allowPlaintextJournalFallback?: boolean;
}

export function createCapabilityService(
  config: CreateCapabilityServiceConfig,
): CapabilityService {
  const workspace = config.workspace;
  // C2 has no path-string mutation fallback. A composition that cannot prove
  // all writable prerequisites still receives an authority object so the
  // broker disables legacy mutation endpoints, but its prepare call fails
  // closed with `workspace_write_unsupported`.
  const native = workspace?.native ?? new UnavailableNativeWorkspaceAuthority();
  const workspaceWritableBootstrap =
    workspace !== undefined &&
    workspace.journalIntegrityKey.byteLength >= 32 &&
    native.primitivesAvailable;
  const store = new GrantStore({
    userDataDir: config.userDataDir,
    safeStorage: config.safeStorage,
    allowPlaintextFallback: config.allowPlaintextFallback,
    audit: config.audit,
    rootIdentity: workspaceWritableBootstrap
      ? (root) => native.rootIdentity(root)
      : undefined,
    profileId: workspace?.profileId,
    profileIdResolver: workspace?.profileIdResolver,
    deviceId: workspace?.deviceId,
  });
  const authority = new LocalWorkspaceAuthority({
    grants: store,
    native: workspaceWritableBootstrap
      ? native
      : new UnavailableNativeWorkspaceAuthority(),
    journal:
      workspaceWritableBootstrap && workspace !== undefined
        ? new EncryptedWorkspaceJournalStore({
            userDataDir: config.userDataDir,
            safeStorage: config.safeStorage,
            integrityKey: workspace.journalIntegrityKey,
            allowPlaintextFallback:
              !workspace.production &&
              workspace.allowPlaintextJournalFallback === true,
          })
        : new InMemoryWorkspaceJournalStore(),
    attestation:
      workspaceWritableBootstrap && workspace !== undefined
        ? workspace.attestation
        : {
            workspaceWriteIsolation: "unavailable",
            nativeWorkspacePrimitives: "unavailable",
          },
    production: workspace?.production ?? true,
    deviceId: workspace?.deviceId ?? "workspace-authority-unbound",
    journalTokenKey: workspace?.journalIntegrityKey,
  });
  const picker = new FolderPicker({ showOpenDialog: config.showOpenDialog });
  // The broker's FS routes execute reads through HostFs; without it they fail
  // closed. HostFs itself is stateless — it only ever touches paths under a
  // grant root the broker resolves from the store.
  const broker = new CapabilityBroker({
    grants: store,
    hostFs: new HostFs(),
    workspaceAuthority: authority,
    clientCredentials: config.localBrokerClients,
    mcpSecrets: config.mcpSecrets,
  });
  return new CapabilityService({
    store,
    picker,
    broker,
    workspaceAuthority: authority,
  });
}

export {
  CAPABILITY_CHANNELS,
  CAPABILITY_CHANNEL_VALUES,
  isCapabilityChannel,
  type CapabilityChannelName,
} from "./channels";
export { CapabilityService } from "./service";
export {
  CapabilityBroker,
  CAPABILITY_BROKER_AUDIENCE,
  CAPABILITY_BROKER_PROTOCOL,
  type McpConnectionSecret,
  type McpSecretStore,
} from "./broker";
export { GrantStore } from "./grant-store";
export {
  EncryptedWorkspaceJournalStore,
  type EncryptedWorkspaceJournalConfig,
} from "./workspace-journal";
export {
  NativeWorkspaceCommitHelper,
  NativeWorkspaceCommitHelperError,
  resolveNativeWorkspaceCommitHelperPath,
  type NativeWorkspaceCommitHelperConfig,
} from "./native-workspace-commit-helper";
export {
  AddonNativeWorkspaceAuthority,
  UnavailableNativeWorkspaceAuthority,
  hasNativeWorkspaceV2Bindings,
  type NativeWorkspaceV2Bindings,
} from "./native-workspace-authority";
export {
  DESKTOP_WORKSPACE_ATTESTATION_PATH,
  DESKTOP_WORKSPACE_ATTESTATION_TTL_MS,
  DESKTOP_WORKSPACE_ATTESTATION_VERSION,
  DesktopWorkspaceAttestationError,
  DesktopWorkspaceAttestationPublisher,
  canonicalClaimsJson,
  type DesktopWorkspaceAttestationBootstrap,
  type DesktopWorkspaceAttestationClaims,
  type DesktopWorkspaceAttestationEnvelope,
  type DesktopWorkspaceAttestationPublisherDeps,
} from "./workspace-attestation";
export {
  InMemoryWorkspaceJournalStore,
  LocalWorkspaceAuthority,
  WorkspaceAuthorityError,
  type LocalWorkspaceAuthorityConfig,
  type NativePreparedWorkspace,
  type NativeWorkspaceAuthority,
  type NativeWorkspaceCommitResult,
  type WorkspaceChangeEntry,
  type WorkspaceChangeSet,
  type WorkspaceCommitOutcome,
  type WorkspaceCommitPermit,
  type WorkspaceCommitResult,
  type WorkspaceJournalRecord,
  type WorkspaceJournalState,
  type WorkspaceJournalStore,
  type WorkspacePreparedBinding,
  type WorkspacePreparedEffect,
  type WorkspacePrivateHostSession,
  type WorkspaceReadCapability,
  type WorkspaceRootIdentity,
  type WorkspaceRunFacts,
  type WorkspaceWriteAttestation,
} from "./workspace-authority";
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
  FORBIDDEN_ROOT_MESSAGES,
  SENSITIVE_ROOT_SEGMENTS,
  SENSITIVE_FILE_RULES,
  SYSTEM_ROOT_SEGMENTS,
  VOLUME_PARENT_SEGMENTS,
  HOME_CONTAINER_SEGMENTS,
  APPLICATION_STATE_SEGMENTS,
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
  toHostSessionGrant,
  type HostSessionGrant,
  type Grant,
  type GrantMode,
  type GrantProvider,
  type GrantRootIdentity,
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

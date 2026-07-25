// AC8 agentic browser — subsystem barrel + composition root.
//
// Public surface of the desktop agentic-browser subsystem. `main/index.ts`
// builds it ONLY when `isDesktopBrowserEnabled(process.env)` is true (gated
// off by default). It ships supervised worker lifecycle, authenticated broker
// and worker RPC, exact-origin read authority, and the first exact staged
// effect cohort (click/submit). Downloads and artifact-backed uploads remain a
// later D4 slice.

export * from "./protocol";
export * from "./feature-gate";
export {
  evaluateUrlShape,
  evaluateResolvedAddress,
  evaluateHostName,
  evaluateIpv4,
  evaluateIpv6,
  parseIpv4,
  parseIpv6,
  type EgressDecision,
} from "./egress-policy";
export {
  EgressProxy,
  hostsFromOrigins,
  type ConnectDecision,
  type DnsResolver,
  type ResolvedAddress,
} from "./network-policy-proxy";
export {
  ProfileStore,
  ProfileError,
  type ProfileManifest,
  type ProfileFsPort,
} from "./profile-store";
export {
  StagingArea,
  type StagingFsPort,
  type StagedArtifact,
  type StagedKind,
} from "./staging";
export {
  sanitizeDownloadName,
  downloadExtension,
  evaluateDownloadPolicy,
  sha256Hex,
  type DownloadPolicyDecision,
} from "./downloads";
export {
  BrowserApprovalDecision,
  toolRequiresApproval,
  type BrowserApprovalPort,
  type BrowserApprovalRequest,
} from "./action-policy";
export { BrowserSession, type BrowserSessionConfig } from "./browser-session";
export {
  BrowserWorkerSupervisor,
  FatalBrowserWorker,
  computeBackoffDelayMs,
  type BrowserWorkerState,
  type WorkerHealth,
} from "./browser-supervisor";
export {
  BrowserBroker,
  BROWSER_BROKER_PROTOCOL,
  type BrowserWorkerPort,
} from "./browser-broker";
export {
  BrowserPrivateEffectBridge,
  type BrowserPrivateEffectWorkerPort,
} from "./private-effect-bridge";
export {
  MainBrowserReadAuthority,
  BrowserReadAuthorityError,
  type BrowserReadAuthority,
} from "./read-authority";
export {
  SessionWorkerPort,
  type SessionWorkerPortConfig,
} from "./session-worker-port";
export {
  BrowserWorkerRpcClient,
  BrowserWorkerRpcError,
  BROWSER_WORKER_RPC_PROTOCOL,
  BROWSER_WORKER_RPC_TOKEN_ENV,
  installBrowserWorkerRpcServer,
  type BrowserWorkerIpcChild,
} from "./worker-rpc";
export {
  BROWSER_TOOL_SCHEMAS,
  BROWSER_ACTION_TOOL_SCHEMAS,
  browserToolSchemas,
  type BrowserToolSchema,
} from "./tool-schemas";
export {
  createPlaywrightEngine,
  type BrowserEngine,
  type EngineContext,
  type EnginePage,
  type ElementTarget,
  type DownloadCapture,
  type RawAxNode,
} from "./browser-engine";
export {
  createProductionDesktopBrowserSubsystem,
  readOriginPolicy,
  type ProductionDesktopBrowserConfig,
  type ProductionDesktopBrowserSubsystem,
  type SpawnBrowserWorker,
} from "./desktop-runtime";
export {
  resolveBrowserExecutablePath,
  BrowserRuntimeError,
  BROWSER_RUNTIME_MANIFEST,
  BROWSER_RUNTIME_SCHEMA_VERSION,
  type BrowserRuntimeConfig,
} from "./browser-runtime";

import {
  BrowserBroker,
  type BrowserBrokerHandle,
  type BrowserWorkerPort,
} from "./browser-broker";
import type { BrowserWorkerSupervisor } from "./browser-supervisor";
import type { BrowserPrivateEffectWorkerPort } from "./private-effect-bridge";
import type { BrowserReadAuthority } from "./read-authority";

export interface DesktopBrowserSubsystem {
  readonly broker: BrowserBroker;
  readonly workerPort: BrowserWorkerPort & BrowserPrivateEffectWorkerPort;
  readonly supervisor: BrowserWorkerSupervisor;
  start(): Promise<BrowserBrokerHandle>;
  stop(): Promise<void>;
}

/**
 * Compose the production browser topology from an authenticated worker RPC
 * port, its lifecycle supervisor, and Electron-main read authority. There is
 * intentionally no in-process SessionWorkerPort fallback: Playwright cannot
 * enter Electron main by accident.
 */
export function buildDesktopBrowserSubsystem(deps: {
  readonly workerPort: BrowserWorkerPort &
    BrowserPrivateEffectWorkerPort & {
      closeAll(): Promise<void>;
      dispose?(): void;
    };
  readonly supervisor: BrowserWorkerSupervisor;
  readonly readAuthority: BrowserReadAuthority;
}): DesktopBrowserSubsystem {
  const broker = new BrowserBroker({
    worker: deps.workerPort,
    readAuthority: deps.readAuthority,
    privateEffects: deps.workerPort,
  });
  return {
    broker,
    workerPort: deps.workerPort,
    supervisor: deps.supervisor,
    async start() {
      try {
        await deps.supervisor.start();
        if (!deps.supervisor.isHealthy()) {
          throw new Error("browser worker did not become healthy");
        }
        return await broker.start();
      } catch (err) {
        if (broker.isRunning()) await broker.stop();
        await deps.supervisor.stop();
        deps.workerPort.dispose?.();
        throw err;
      }
    },
    async stop() {
      await broker.stop();
      try {
        await deps.workerPort.closeAll();
      } catch {
        // A crashed worker has no remaining live authority.
      }
      await deps.supervisor.stop();
      deps.workerPort.dispose?.();
    },
  };
}

// AC8 agentic browser — subsystem barrel + composition root.
//
// Public surface of the desktop agentic-browser foundation. `main/index.ts`
// builds the subsystem ONLY when `isDesktopBrowserEnabled(process.env)` is
// true (gated off by default). This foundation ships the read-only core:
// supervised worker lifecycle, loopback broker, egress policy proxy, profile
// isolation, and the typed read-only session (navigate / snapshot / screenshot
// / wait / close). Downloads, uploads, and side-effecting actions are DEFERRED.

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
  SessionWorkerPort,
  type SessionWorkerPortConfig,
} from "./session-worker-port";
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

import { BrowserBroker, type BrowserBrokerHandle } from "./browser-broker";
import { SessionWorkerPort } from "./session-worker-port";
import type { BrowserSession } from "./browser-session";
import type { BrowserActionRequest } from "./protocol";

export interface DesktopBrowserSubsystem {
  readonly broker: BrowserBroker;
  readonly workerPort: SessionWorkerPort;
  start(): Promise<BrowserBrokerHandle>;
  stop(): Promise<void>;
}

/**
 * Compose the AI-facing edge of the browser subsystem: the loopback broker in
 * front of a `SessionWorkerPort`. `createSession` is injected by main (it owns
 * profile paths + the egress proxy + the browser engine), keeping this
 * composition free of OS specifics and unit-testable. The supervised worker
 * child is managed separately by `BrowserWorkerSupervisor`; wiring the broker's
 * port to the OS child's RPC transport is the next slice's seam.
 */
export function buildDesktopBrowserSubsystem(deps: {
  readonly createSession: (
    binding: BrowserActionRequest["binding"],
  ) => Promise<BrowserSession>;
  /**
   * Advertise the side-effecting action layer. Only pass true when
   * `createSession` composes sessions with an approval authority. Default false
   * keeps the read-only surface.
   */
  readonly includeActionTools?: boolean;
}): DesktopBrowserSubsystem {
  const workerPort = new SessionWorkerPort({
    createSession: deps.createSession,
    includeActionTools: deps.includeActionTools,
  });
  const broker = new BrowserBroker({ worker: workerPort });
  return {
    broker,
    workerPort,
    start: () => broker.start(),
    async stop() {
      await broker.stop();
      await workerPort.closeAll();
    },
  };
}

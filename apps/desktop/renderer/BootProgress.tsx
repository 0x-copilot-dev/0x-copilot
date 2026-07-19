import { useEffect, useState, type ReactElement, type ReactNode } from "react";

import { BrandMark } from "@0x-copilot/chat-surface";
import {
  BootStatusPayloadSchema,
  CHANNELS,
  type BootPhase,
  type BootStatusPayload,
  type WindowBridge,
} from "@0x-copilot/chat-transport";

import "./BootProgress.css";

export interface BootGateProps {
  readonly bridge: WindowBridge;
  readonly children: ReactNode;
}

// The supervised boot phases, in order, with the "shows its work" log line each
// one prints. The headline + percent come live from the supervisor payload
// (`message`/`percent`); this list drives the step log's done/active/future
// states. `ready`/`stopping` are terminal and never appear as steps.
const PHASES: ReadonlyArray<{
  readonly phase: BootPhase;
  readonly log: string;
  readonly sub: string;
}> = [
  { phase: "secrets", log: "Unlocking secure storage", sub: "keychain" },
  { phase: "ports", log: "Preparing your workspace", sub: "local" },
  { phase: "postgres", log: "Starting the local database", sub: "on-device" },
  { phase: "migrations", log: "Setting up the database", sub: "schema" },
  { phase: "services", log: "Starting 0xCopilot", sub: "runtime" },
  { phase: "health", log: "Finishing up", sub: "almost ready" },
];

// Minimal boot screen shown until main pushes `{ phase: "ready" }` on the
// boot.status channel. Supervised (packaged) boots stream real progress; dev
// mode receives a synthetic ready immediately after load. A payload with
// `fatal: true` swaps to a terminal error screen — there is nothing the
// renderer can retry when the local services cannot come up.
export function BootGate(props: BootGateProps): ReactNode {
  const { bridge, children } = props;
  const [status, setStatus] = useState<BootStatusPayload | null>(null);

  useEffect(() => {
    return bridge.ipc.on(CHANNELS.bootStatus, (payload: unknown) => {
      const parsed = BootStatusPayloadSchema.safeParse(payload);
      if (!parsed.success) return;
      setStatus((prev) => {
        // Never regress out of a fatal terminal state.
        if (prev?.fatal === true) return prev;
        return parsed.data;
      });
    });
  }, [bridge]);

  if (status?.fatal === true) {
    return (
      <BootChrome>
        <div className="boot-fatal">
          <div className="boot-fatal__badge" aria-hidden="true">
            <AlertGlyph />
          </div>
          <div
            className="boot-fatal__title"
            data-testid="boot-fatal"
            role="alert"
          >
            0xCopilot could not start
          </div>
          <p className="boot-fatal__msg" data-testid="boot-fatal-message">
            {status.message}
          </p>
          <p className="boot-fatal__hint">
            Check the logs in the app data folder, then relaunch. If it keeps
            failing, run <b>copilot repair</b>.
          </p>
        </div>
      </BootChrome>
    );
  }

  if (status?.phase === "ready") {
    return children;
  }

  // Active phase index → drives the step log + a synthetic percent fallback.
  const activeIndex =
    status === null
      ? 0
      : Math.max(
          0,
          PHASES.findIndex((p) => p.phase === status.phase),
        );
  const active = PHASES[Math.min(activeIndex, PHASES.length - 1)];
  const percent =
    status?.percent ?? Math.round((activeIndex / PHASES.length) * 100);
  const title = status?.message ?? "Starting 0xCopilot…";
  const sub = active?.sub ?? "starting";

  return (
    <BootChrome>
      <div className="boot-card">
        <div className="boot-mark" aria-hidden="true">
          <span className="ring" />
          <span className="boot-spin">
            <BrandMark size={40} />
          </span>
        </div>
        <div className="boot-title" data-testid="boot-message">
          {title}
        </div>
        <div className="boot-sub">{sub}</div>
        <div
          className="boot-bar"
          data-testid="boot-progress"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
        >
          <div className="boot-bar__f" style={{ width: `${percent}%` }} />
        </div>
        <div className="boot-log">
          {PHASES.map((p, k) => {
            const state =
              k < activeIndex
                ? "done"
                : k === activeIndex
                  ? "active"
                  : "future";
            return (
              <div key={p.phase} className="boot-line" data-state={state}>
                <span className="bic">
                  {state === "done" ? (
                    <CheckGlyph />
                  ) : state === "active" ? (
                    <span className="boot-spinner" />
                  ) : (
                    <span className="bdot" />
                  )}
                </span>
                <span>{p.log}</span>
              </div>
            );
          })}
        </div>
      </div>
      <div className="boot-foot">
        <span>local build</span>
        <span>nothing leaves this machine</span>
      </div>
    </BootChrome>
  );
}

function BootChrome({
  children,
}: {
  readonly children: ReactNode;
}): ReactElement {
  return (
    <div className="boot" data-testid="boot-gate" data-theme="dark">
      {children}
    </div>
  );
}

function CheckGlyph(): ReactElement {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
    >
      <path d="M20 6 9 17l-5-5" />
    </svg>
  );
}

function AlertGlyph(): ReactElement {
  return (
    <svg
      viewBox="0 0 24 24"
      width={30}
      height={30}
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
    >
      <path d="M12 8v5" />
      <path d="M12 16.5h.01" />
      <path d="M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" />
    </svg>
  );
}

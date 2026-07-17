import { useEffect, useState, type ReactNode } from "react";

import {
  BootStatusPayloadSchema,
  CHANNELS,
  type BootStatusPayload,
  type WindowBridge,
} from "@0x-copilot/chat-transport";

export interface BootGateProps {
  readonly bridge: WindowBridge;
  readonly children: ReactNode;
}

// Minimal boot screen shown until main pushes `{ phase: "ready" }` on the
// boot.status channel. Supervised (packaged) boots stream real progress;
// dev mode receives a synthetic ready immediately after load. A payload
// with `fatal: true` swaps to a terminal error screen — there is nothing
// the renderer can retry when the local services cannot come up.
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
        <p data-testid="boot-fatal" role="alert">
          Atlas could not start
        </p>
        <p data-testid="boot-fatal-message" style={{ maxWidth: "36rem" }}>
          {status.message}
        </p>
        <p style={{ opacity: 0.7 }}>
          Check the logs directory in the app data folder, then relaunch.
        </p>
      </BootChrome>
    );
  }

  if (status?.phase === "ready") {
    return children;
  }

  const percent = status?.percent ?? 0;
  const message = status?.message ?? "Starting Atlas…";
  return (
    <BootChrome>
      <p data-testid="boot-message">{message}</p>
      <div
        data-testid="boot-progress"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        style={{
          width: "16rem",
          height: "0.375rem",
          borderRadius: "0.25rem",
          background: "#2a2c30",
          overflow: "hidden",
        }}
      >
        <div
          style={{
            width: `${percent}%`,
            height: "100%",
            background: "#dde",
            transition: "width 200ms ease",
          }}
        />
      </div>
    </BootChrome>
  );
}

function BootChrome({ children }: { readonly children: ReactNode }): ReactNode {
  return (
    <div
      data-testid="boot-gate"
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: "1rem",
        height: "100vh",
        fontFamily: "system-ui, sans-serif",
        color: "#dde",
        background: "#101113",
      }}
    >
      {children}
    </div>
  );
}

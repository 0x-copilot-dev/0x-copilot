import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from "react";

import {
  isRuntimeEventEnvelope,
  type RuntimeEventEnvelope,
} from "@enterprise-search/api-types";

import { useKeyValueStore } from "../providers/KeyValueStoreProvider";
import { useTransport } from "../providers/TransportProvider";

export type Playhead = "now" | { readonly at: number };

export interface TcSwimlanesProps {
  readonly runId: string;
  readonly onScrubChange?: (playhead: Playhead) => void;
  readonly onBranch?: (atMillis: number) => void;
  readonly onRestore?: (atMillis: number) => void;
}

interface Bead {
  readonly id: string;
  readonly at: number;
  readonly lane: string;
  readonly title: string;
}

const SYSTEM_LANE = "system";
const PLAY_INTERVAL_MS = 500;

const PALETTE = {
  lime: "#c2ff5a",
  limeShadow: "rgba(194, 255, 90, 0.18)",
  cardBg: "#181a1c",
  cardBorder: "#2a2d31",
  laneBg: "#1f2225",
  textHi: "#f4f5f6",
  textLo: "#9aa0a6",
  pinned: "#f5c542",
} as const;

function laneFromEvent(event: RuntimeEventEnvelope): string {
  const candidate = event.payload?.["surface_uri"];
  if (typeof candidate !== "string") {
    return SYSTEM_LANE;
  }
  const sepIndex = candidate.indexOf("://");
  if (sepIndex <= 0) {
    return SYSTEM_LANE;
  }
  return candidate.slice(0, sepIndex);
}

function titleFromEvent(event: RuntimeEventEnvelope): string {
  return (
    event.display_title ?? event.presentation?.title ?? event.event_type ?? ""
  );
}

function toBead(event: RuntimeEventEnvelope): Bead | null {
  const parsed = Date.parse(event.created_at);
  if (Number.isNaN(parsed)) {
    return null;
  }
  return {
    id: event.event_id,
    at: parsed,
    lane: laneFromEvent(event),
    title: titleFromEvent(event),
  };
}

function sortLanes(lanes: readonly string[]): readonly string[] {
  const withoutSystem = lanes.filter((lane) => lane !== SYSTEM_LANE).sort();
  return lanes.includes(SYSTEM_LANE)
    ? [...withoutSystem, SYSTEM_LANE]
    : withoutSystem;
}

function pinnedKey(runId: string): string {
  return `swimlanes:pinned:${runId}`;
}

function loadPinned(raw: string | null): ReadonlySet<string> {
  if (raw === null) {
    return new Set();
  }
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return new Set();
    }
    return new Set(
      parsed.filter((entry): entry is string => typeof entry === "string"),
    );
  } catch {
    return new Set();
  }
}

function serializePinned(pinned: ReadonlySet<string>): string | null {
  if (pinned.size === 0) {
    return null;
  }
  return JSON.stringify([...pinned]);
}

export function TcSwimlanes(props: TcSwimlanesProps): ReactNode {
  const { runId, onScrubChange, onBranch, onRestore } = props;
  const transport = useTransport();
  const kvStore = useKeyValueStore();

  const [beads, setBeads] = useState<readonly Bead[]>([]);
  const [playhead, setPlayhead] = useState<Playhead>("now");
  const [isPlaying, setIsPlaying] = useState(false);
  const [pinned, setPinned] = useState<ReadonlySet<string>>(() =>
    loadPinned(kvStore.get(pinnedKey(runId))),
  );

  const playheadRef = useRef<Playhead>(playhead);
  playheadRef.current = playhead;

  const updatePlayhead = useCallback(
    (next: Playhead) => {
      setPlayhead(next);
      onScrubChange?.(next);
    },
    [onScrubChange],
  );

  useEffect(() => {
    setBeads([]);
    setPlayhead("now");
    setIsPlaying(false);
    setPinned(loadPinned(kvStore.get(pinnedKey(runId))));
  }, [runId, kvStore]);

  useEffect(() => {
    const subscription = transport.subscribeServerSentEvents({
      path: `/v1/agent/runs/${runId}/stream`,
      onMessage: (raw) => {
        let parsed: unknown;
        try {
          parsed = JSON.parse(raw);
        } catch {
          return;
        }
        if (!isRuntimeEventEnvelope(parsed)) {
          return;
        }
        const bead = toBead(parsed);
        if (bead === null) {
          return;
        }
        setBeads((prev) => {
          if (prev.some((existing) => existing.id === bead.id)) {
            return prev;
          }
          const merged = [...prev, bead];
          merged.sort((a, b) => a.at - b.at);
          return merged;
        });
      },
    });
    return () => subscription.close();
  }, [transport, runId]);

  const sortedBeads = beads;

  const lanes = useMemo(() => {
    const unique = new Set<string>();
    for (const bead of sortedBeads) {
      unique.add(bead.lane);
    }
    return sortLanes([...unique]);
  }, [sortedBeads]);

  const playheadIndex = useMemo(() => {
    if (playhead === "now") {
      return sortedBeads.length - 1;
    }
    let best = -1;
    for (let i = 0; i < sortedBeads.length; i += 1) {
      if (sortedBeads[i].at <= playhead.at) {
        best = i;
      } else {
        break;
      }
    }
    return best;
  }, [playhead, sortedBeads]);

  const step = useCallback(
    (direction: -1 | 1) => {
      if (sortedBeads.length === 0) {
        return;
      }
      const currentIndex =
        playhead === "now" ? sortedBeads.length - 1 : playheadIndex;
      const target = currentIndex + direction;
      if (target < 0) {
        return;
      }
      if (target >= sortedBeads.length) {
        updatePlayhead("now");
        return;
      }
      updatePlayhead({ at: sortedBeads[target].at });
    },
    [playhead, playheadIndex, sortedBeads, updatePlayhead],
  );

  useEffect(() => {
    if (!isPlaying) {
      return;
    }
    if (sortedBeads.length === 0) {
      setIsPlaying(false);
      return;
    }
    const handle = setInterval(() => {
      const current = playheadRef.current;
      if (current === "now") {
        setIsPlaying(false);
        return;
      }
      let nextIndex = 0;
      for (let i = 0; i < sortedBeads.length; i += 1) {
        if (sortedBeads[i].at <= current.at) {
          nextIndex = i + 1;
        } else {
          break;
        }
      }
      if (nextIndex >= sortedBeads.length) {
        updatePlayhead("now");
        setIsPlaying(false);
        return;
      }
      updatePlayhead({ at: sortedBeads[nextIndex].at });
    }, PLAY_INTERVAL_MS);
    return () => clearInterval(handle);
  }, [isPlaying, sortedBeads, updatePlayhead]);

  const togglePin = useCallback(
    (eventId: string) => {
      setPinned((prev) => {
        const next = new Set(prev);
        if (next.has(eventId)) {
          next.delete(eventId);
        } else {
          next.add(eventId);
        }
        kvStore.set(pinnedKey(runId), serializePinned(next));
        return next;
      });
    },
    [kvStore, runId],
  );

  const handleBeadClick = useCallback(
    (bead: Bead) => {
      updatePlayhead({ at: bead.at });
    },
    [updatePlayhead],
  );

  const snapToNow = useCallback(() => {
    setIsPlaying(false);
    updatePlayhead("now");
  }, [updatePlayhead]);

  const togglePlay = useCallback(() => {
    if (sortedBeads.length === 0) {
      return;
    }
    if (playheadRef.current === "now") {
      if (sortedBeads.length > 0) {
        updatePlayhead({ at: sortedBeads[0].at });
      }
    }
    setIsPlaying((prev) => !prev);
  }, [sortedBeads, updatePlayhead]);

  const handleKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key === "ArrowLeft") {
        event.preventDefault();
        step(-1);
        return;
      }
      if (event.key === "ArrowRight") {
        event.preventDefault();
        step(1);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        snapToNow();
      }
    },
    [step, snapToNow],
  );

  const handleBranch = useCallback(() => {
    if (playhead === "now") {
      return;
    }
    const at = playhead.at;
    void transport
      .request({
        method: "POST",
        path: `/v1/agent/runs/${runId}/branch`,
        query: { at },
      })
      .then(() => {
        onBranch?.(at);
      })
      .catch((err: unknown) => {
        console.warn("TcSwimlanes: branch request failed", err);
      });
  }, [playhead, transport, runId, onBranch]);

  const handleRestore = useCallback(() => {
    if (playhead === "now") {
      return;
    }
    const at = playhead.at;
    void transport
      .request({
        method: "POST",
        path: `/v1/agent/runs/${runId}/restore`,
        query: { at },
      })
      .then(() => {
        onRestore?.(at);
      })
      .catch((err: unknown) => {
        console.warn("TcSwimlanes: restore request failed", err);
      });
  }, [playhead, transport, runId, onRestore]);

  const minAt = sortedBeads.length > 0 ? sortedBeads[0].at : 0;
  const maxAt =
    sortedBeads.length > 0 ? sortedBeads[sortedBeads.length - 1].at : 0;
  const span = Math.max(1, maxAt - minAt);

  const handleLaneClick = useCallback(
    (event: MouseEvent<HTMLDivElement>) => {
      if (sortedBeads.length === 0) {
        return;
      }
      const target = event.currentTarget;
      const rect = target.getBoundingClientRect();
      const ratio = Math.max(
        0,
        Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)),
      );
      const targetAt = minAt + ratio * span;
      let nearest = sortedBeads[0];
      let nearestDistance = Math.abs(nearest.at - targetAt);
      for (const bead of sortedBeads) {
        const dist = Math.abs(bead.at - targetAt);
        if (dist < nearestDistance) {
          nearest = bead;
          nearestDistance = dist;
        }
      }
      updatePlayhead({ at: nearest.at });
    },
    [sortedBeads, minAt, span, updatePlayhead],
  );

  const playheadLeftPercent =
    playhead === "now"
      ? 100
      : Math.max(0, Math.min(100, ((playhead.at - minAt) / span) * 100));

  const isOffNow = playhead !== "now";
  const hasBeads = sortedBeads.length > 0;

  return (
    <div
      role="region"
      aria-label="Swimlane timeline"
      data-testid="tc-swimlanes"
      data-playhead={playhead === "now" ? "now" : "scrubbed"}
      tabIndex={0}
      onKeyDown={handleKeyDown}
      style={containerStyle}
    >
      <div role="toolbar" aria-label="Timeline controls" style={toolbarStyle}>
        <button
          type="button"
          onClick={() => step(-1)}
          disabled={!hasBeads}
          data-testid="tc-swimlanes-back"
          style={buttonStyle}
          aria-label="Step back"
        >
          {"<"}
        </button>
        <button
          type="button"
          onClick={togglePlay}
          disabled={!hasBeads}
          data-testid="tc-swimlanes-play"
          style={buttonStyle}
          aria-label={isPlaying ? "Pause" : "Play"}
        >
          {isPlaying ? "Pause" : "Play"}
        </button>
        <button
          type="button"
          onClick={() => step(1)}
          disabled={!hasBeads}
          data-testid="tc-swimlanes-forward"
          style={buttonStyle}
          aria-label="Step forward"
        >
          {">"}
        </button>
        {isOffNow ? (
          <button
            type="button"
            onClick={snapToNow}
            data-testid="tc-swimlanes-snap-now"
            style={buttonStyle}
          >
            Snap to now
          </button>
        ) : null}
        {isOffNow ? (
          <>
            <button
              type="button"
              onClick={handleBranch}
              data-testid="tc-swimlanes-branch"
              style={primaryButtonStyle}
            >
              Branch from here
            </button>
            <button
              type="button"
              onClick={handleRestore}
              data-testid="tc-swimlanes-restore"
              style={primaryButtonStyle}
            >
              Restore this state
            </button>
          </>
        ) : null}
      </div>

      {!hasBeads ? (
        <div
          role="status"
          data-testid="tc-swimlanes-empty"
          style={emptyStateStyle}
        >
          Listening for run events…
        </div>
      ) : (
        <div style={lanesContainerStyle}>
          {lanes.map((lane) => {
            const laneBeads = sortedBeads.filter((bead) => bead.lane === lane);
            return (
              <div
                key={lane}
                data-testid={`tc-swimlanes-lane-${lane}`}
                data-lane={lane}
                style={laneRowStyle}
              >
                <div style={laneLabelStyle}>{lane}</div>
                <div
                  data-testid={`tc-swimlanes-lane-track-${lane}`}
                  style={laneTrackStyle}
                  onClick={handleLaneClick}
                  role="presentation"
                >
                  {laneBeads.map((bead) => {
                    const leftPercent = ((bead.at - minAt) / span) * 100;
                    const isPinned = pinned.has(bead.id);
                    return (
                      <div
                        key={bead.id}
                        data-testid={`tc-swimlanes-bead-${bead.id}`}
                        data-bead-id={bead.id}
                        data-pinned={isPinned ? "true" : "false"}
                        style={beadStyle(leftPercent, isPinned)}
                      >
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            handleBeadClick(bead);
                          }}
                          data-testid={`tc-swimlanes-bead-select-${bead.id}`}
                          style={beadButtonStyle}
                          aria-label={`Bead ${bead.title}`}
                          title={bead.title}
                        />
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            togglePin(bead.id);
                          }}
                          data-testid={`tc-swimlanes-bead-pin-${bead.id}`}
                          aria-pressed={isPinned}
                          aria-label={isPinned ? "Unpin bead" : "Pin bead"}
                          style={pinButtonStyle(isPinned)}
                        >
                          {isPinned ? "*" : "+"}
                        </button>
                      </div>
                    );
                  })}
                  <div
                    data-testid="tc-swimlanes-playhead"
                    style={playheadStyle(playheadLeftPercent)}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const containerStyle: CSSProperties = {
  background: PALETTE.cardBg,
  border: `1px solid ${PALETTE.cardBorder}`,
  borderRadius: 10,
  padding: 12,
  color: PALETTE.textHi,
  display: "flex",
  flexDirection: "column",
  gap: 10,
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  outline: "none",
};

const toolbarStyle: CSSProperties = {
  display: "flex",
  gap: 8,
  alignItems: "center",
  flexWrap: "wrap",
};

const buttonStyle: CSSProperties = {
  background: "transparent",
  color: PALETTE.textHi,
  border: `1px solid ${PALETTE.cardBorder}`,
  borderRadius: 6,
  padding: "4px 10px",
  fontSize: 12,
  cursor: "pointer",
};

const primaryButtonStyle: CSSProperties = {
  background: PALETTE.lime,
  color: PALETTE.cardBg,
  border: "none",
  borderRadius: 6,
  padding: "4px 10px",
  fontSize: 12,
  fontWeight: 600,
  cursor: "pointer",
};

const emptyStateStyle: CSSProperties = {
  color: PALETTE.textLo,
  fontSize: 12,
  padding: 12,
};

const lanesContainerStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
};

const laneRowStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
};

const laneLabelStyle: CSSProperties = {
  width: 96,
  fontSize: 11,
  color: PALETTE.textLo,
  textTransform: "lowercase",
};

const laneTrackStyle: CSSProperties = {
  position: "relative",
  flex: 1,
  height: 28,
  background: PALETTE.laneBg,
  borderRadius: 6,
  cursor: "pointer",
};

const beadStyle = (leftPercent: number, isPinned: boolean): CSSProperties => ({
  position: "absolute",
  left: `${leftPercent}%`,
  top: "50%",
  transform: "translate(-50%, -50%)",
  display: "flex",
  alignItems: "center",
  gap: 2,
  background: isPinned ? PALETTE.pinned : PALETTE.lime,
  borderRadius: 999,
  padding: "2px 4px",
  boxShadow: `0 0 8px ${PALETTE.limeShadow}`,
});

const beadButtonStyle: CSSProperties = {
  width: 10,
  height: 10,
  borderRadius: 999,
  border: "none",
  background: "transparent",
  cursor: "pointer",
};

const pinButtonStyle = (isPinned: boolean): CSSProperties => ({
  background: isPinned ? PALETTE.cardBg : "transparent",
  color: isPinned ? PALETTE.pinned : PALETTE.cardBg,
  border: "none",
  borderRadius: 999,
  cursor: "pointer",
  fontSize: 9,
  lineHeight: 1,
  padding: 0,
  width: 12,
  height: 12,
});

const playheadStyle = (leftPercent: number): CSSProperties => ({
  position: "absolute",
  left: `${leftPercent}%`,
  top: 0,
  bottom: 0,
  width: 2,
  background: PALETTE.lime,
  pointerEvents: "none",
});

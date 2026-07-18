import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
} from "react";

import {
  isRuntimeEventEnvelope,
  type RuntimeEventEnvelope,
} from "@0x-copilot/api-types";

import { useKeyValueStore } from "../providers/KeyValueStoreProvider";
import { useTransport } from "../providers/TransportProvider";
import { swimlaneStyles } from "./TcSwimlanes.styles";
import { TcSwimlanesTransportControls } from "./TcSwimlanesTransportControls";

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
const SUBAGENT_LANE_PREFIX = "subagent:";
const PLAY_INTERVAL_MS = 500;

function laneFromEvent(event: RuntimeEventEnvelope): string {
  // PR-3.8 (FR-3.14 / FR-3.17b) — one live lane per subagent. Subagent-sourced
  // frames group by their subagent identity so each dispatched subagent gets
  // its own lane, independent of any surface it writes to. Non-subagent frames
  // keep their surface-scheme lane (or `system`), unchanged.
  if (event.source === "subagent") {
    const id = subagentLaneId(event);
    if (id !== null) {
      return `${SUBAGENT_LANE_PREFIX}${id}`;
    }
  }
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

function subagentLaneId(event: RuntimeEventEnvelope): string | null {
  const subagentId = event.subagent_id?.trim();
  if (subagentId) {
    return subagentId;
  }
  const taskId = event.task_id?.trim();
  return taskId ? taskId : null;
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
      style={swimlaneStyles.container}
    >
      <TcSwimlanesTransportControls
        hasBeads={hasBeads}
        isPlaying={isPlaying}
        isOffNow={isOffNow}
        onStepBack={() => step(-1)}
        onTogglePlay={togglePlay}
        onStepForward={() => step(1)}
        onSnapToNow={snapToNow}
        onBranch={handleBranch}
        onRestore={handleRestore}
      />

      {!hasBeads ? (
        <div
          role="status"
          data-testid="tc-swimlanes-empty"
          style={swimlaneStyles.emptyState}
        >
          Listening for run events…
        </div>
      ) : (
        <div style={swimlaneStyles.lanesContainer}>
          {lanes.map((lane) => {
            const laneBeads = sortedBeads.filter((bead) => bead.lane === lane);
            return (
              <div
                key={lane}
                data-testid={`tc-swimlanes-lane-${lane}`}
                data-lane={lane}
                style={swimlaneStyles.laneRow}
              >
                <div style={swimlaneStyles.laneLabel}>{lane}</div>
                <div
                  data-testid={`tc-swimlanes-lane-track-${lane}`}
                  style={swimlaneStyles.laneTrack}
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
                        style={swimlaneStyles.bead(leftPercent, isPinned)}
                      >
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            handleBeadClick(bead);
                          }}
                          data-testid={`tc-swimlanes-bead-select-${bead.id}`}
                          style={swimlaneStyles.beadButton}
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
                          style={swimlaneStyles.pinButton(isPinned)}
                        >
                          {isPinned ? "*" : "+"}
                        </button>
                      </div>
                    );
                  })}
                  <div
                    data-testid="tc-swimlanes-playhead"
                    style={swimlaneStyles.playhead(playheadLeftPercent)}
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

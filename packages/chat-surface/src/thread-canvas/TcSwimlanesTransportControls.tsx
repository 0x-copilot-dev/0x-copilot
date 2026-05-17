import type { ReactElement } from "react";

import { swimlaneStyles } from "./TcSwimlanes.styles";

export interface TcSwimlanesTransportControlsProps {
  readonly hasBeads: boolean;
  readonly isPlaying: boolean;
  readonly isOffNow: boolean;
  readonly onStepBack: () => void;
  readonly onTogglePlay: () => void;
  readonly onStepForward: () => void;
  readonly onSnapToNow: () => void;
  readonly onBranch: () => void;
  readonly onRestore: () => void;
}

export function TcSwimlanesTransportControls(
  props: TcSwimlanesTransportControlsProps,
): ReactElement {
  const {
    hasBeads,
    isPlaying,
    isOffNow,
    onStepBack,
    onTogglePlay,
    onStepForward,
    onSnapToNow,
    onBranch,
    onRestore,
  } = props;
  return (
    <div
      role="toolbar"
      aria-label="Timeline controls"
      style={swimlaneStyles.toolbar}
    >
      <button
        type="button"
        onClick={onStepBack}
        disabled={!hasBeads}
        data-testid="tc-swimlanes-back"
        style={swimlaneStyles.button}
        aria-label="Step back"
      >
        {"<"}
      </button>
      <button
        type="button"
        onClick={onTogglePlay}
        disabled={!hasBeads}
        data-testid="tc-swimlanes-play"
        style={swimlaneStyles.button}
        aria-label={isPlaying ? "Pause" : "Play"}
      >
        {isPlaying ? "Pause" : "Play"}
      </button>
      <button
        type="button"
        onClick={onStepForward}
        disabled={!hasBeads}
        data-testid="tc-swimlanes-forward"
        style={swimlaneStyles.button}
        aria-label="Step forward"
      >
        {">"}
      </button>
      {isOffNow ? (
        <button
          type="button"
          onClick={onSnapToNow}
          data-testid="tc-swimlanes-snap-now"
          style={swimlaneStyles.button}
        >
          Snap to now
        </button>
      ) : null}
      {isOffNow ? (
        <>
          <button
            type="button"
            onClick={onBranch}
            data-testid="tc-swimlanes-branch"
            style={swimlaneStyles.primaryButton}
          >
            Branch from here
          </button>
          <button
            type="button"
            onClick={onRestore}
            data-testid="tc-swimlanes-restore"
            style={swimlaneStyles.primaryButton}
          >
            Restore this state
          </button>
        </>
      ) : null}
    </div>
  );
}

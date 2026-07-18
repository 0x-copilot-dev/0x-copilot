import type { CSSProperties } from "react";

const PALETTE = {
  lime: "var(--color-accent)",
  limeShadow: "rgba(194, 255, 90, 0.18)",
  cardBg: "#181a1c",
  cardBorder: "#2a2d31",
  laneBg: "#1f2225",
  textHi: "#f4f5f6",
  textLo: "#9aa0a6",
  pinned: "#f5c542",
} as const;

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
  fontSize: "var(--font-size-xs)",
  cursor: "pointer",
};

const primaryButtonStyle: CSSProperties = {
  background: PALETTE.lime,
  color: PALETTE.cardBg,
  border: "none",
  borderRadius: 6,
  padding: "4px 10px",
  fontSize: "var(--font-size-xs)",
  fontWeight: 600,
  cursor: "pointer",
};

const emptyStateStyle: CSSProperties = {
  color: PALETTE.textLo,
  fontSize: "var(--font-size-xs)",
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
  fontSize: "var(--font-size-2xs)",
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

const beadButtonStyle: CSSProperties = {
  width: 10,
  height: 10,
  borderRadius: 999,
  border: "none",
  background: "transparent",
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

const pinButtonStyle = (isPinned: boolean): CSSProperties => ({
  background: isPinned ? PALETTE.cardBg : "transparent",
  color: isPinned ? PALETTE.pinned : PALETTE.cardBg,
  border: "none",
  borderRadius: 999,
  cursor: "pointer",
  fontSize: "var(--font-size-2xs)",
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

export const swimlaneStyles = {
  container: containerStyle,
  toolbar: toolbarStyle,
  button: buttonStyle,
  primaryButton: primaryButtonStyle,
  emptyState: emptyStateStyle,
  lanesContainer: lanesContainerStyle,
  laneRow: laneRowStyle,
  laneLabel: laneLabelStyle,
  laneTrack: laneTrackStyle,
  beadButton: beadButtonStyle,
  bead: beadStyle,
  pinButton: pinButtonStyle,
  playhead: playheadStyle,
} as const;

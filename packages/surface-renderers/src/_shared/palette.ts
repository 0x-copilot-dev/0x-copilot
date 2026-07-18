export const SURFACE_PALETTE = {
  pageBg: "#101113",
  surface: "#181a1c",
  surfaceMute: "#1f2226",
  border: "#2a2d31",
  textHi: "#f4f5f6",
  textMid: "#c8ccd1",
  textLo: "#9aa0a6",
  lime: "var(--color-accent)",
  limeBgSoft: "rgba(194, 255, 90, 0.12)",
} as const;

export type SurfacePalette = typeof SURFACE_PALETTE;

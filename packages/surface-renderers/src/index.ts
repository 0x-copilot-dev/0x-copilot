import { registerEmailSurface } from "./email";

export {
  EmailRenderer,
  EmailDiffOverlay,
  type EmailDiffOverlayProps,
} from "./email";

// === Phase 4-F tier1-slides ===
import { registerSlideAdapter } from "./slide";
export {
  SlideRenderer,
  SlideDiff,
  slideAdapter,
  registerSlideAdapter,
  type Slide,
  type SlideBullet,
  type SlideRendererProps,
  type SlideDiffPayload,
  type SlideDiffProps,
} from "./slide";
// === end Phase 4-F ===

export function registerAll(): void {
  registerEmailSurface();
  // === Phase 4-F tier1-slides ===
  registerSlideAdapter();
  // === end Phase 4-F ===
}

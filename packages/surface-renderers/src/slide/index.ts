import {
  registerAdapter,
  type SaaSRendererAdapter,
} from "@0x-copilot/chat-surface";

import { slideAdapter } from "./SlideDiff";

export { SlideRenderer } from "./SlideRenderer";
export type { Slide, SlideBullet, SlideRendererProps } from "./SlideRenderer";
export { SlideDiff, slideAdapter } from "./SlideDiff";
export type { SlideDiffPayload, SlideDiffProps } from "./SlideDiff";

export function registerSlideAdapter(): void {
  registerAdapter(slideAdapter as SaaSRendererAdapter);
}

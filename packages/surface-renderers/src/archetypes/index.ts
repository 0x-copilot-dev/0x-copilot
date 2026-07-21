import {
  registerAdapter,
  type SaaSRendererAdapter,
} from "@0x-copilot/chat-surface";

import {
  BoardDiffRenderer,
  BoardRenderer,
  boardAdapter,
} from "./BoardRenderer";
import { DocDiffRenderer, DocRenderer, docAdapter } from "./DocRenderer";
import {
  MessageDiffRenderer,
  MessageRenderer,
  messageAdapter,
} from "./MessageRenderer";
import {
  RecordDiffRenderer,
  RecordRenderer,
  recordAdapter,
} from "./RecordRenderer";
import {
  TableDiffRenderer,
  TableRenderer,
  tableAdapter,
} from "./TableRenderer";

export {
  RecordRenderer,
  RecordDiffRenderer,
  recordAdapter,
  TableRenderer,
  TableDiffRenderer,
  tableAdapter,
  MessageRenderer,
  MessageDiffRenderer,
  messageAdapter,
  DocRenderer,
  DocDiffRenderer,
  docAdapter,
  BoardRenderer,
  BoardDiffRenderer,
  boardAdapter,
};

/** The tier-1.5 archetype adapters, in a stable order. */
export const ARCHETYPE_ADAPTERS: readonly SaaSRendererAdapter[] = [
  recordAdapter as SaaSRendererAdapter,
  tableAdapter as SaaSRendererAdapter,
  messageAdapter as SaaSRendererAdapter,
  docAdapter as SaaSRendererAdapter,
  boardAdapter as SaaSRendererAdapter,
];

/**
 * Register the five archetype adapters (`record | table | message | doc |
 * board`). Idempotent: the SurfaceRegistry replaces a same-version entry in
 * place, so calling this twice leaves exactly one adapter per scheme (PRD-03
 * AC5). Archetypes outside this set fall to the tier-3 generic renderer.
 */
export function registerArchetypeAdapters(): void {
  for (const adapter of ARCHETYPE_ADAPTERS) {
    registerAdapter(adapter);
  }
}

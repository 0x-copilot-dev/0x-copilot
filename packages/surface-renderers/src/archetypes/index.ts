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
 * The archetype schemes this package can actually draw, derived from
 * `ARCHETYPE_ADAPTERS` so it cannot drift from the adapters themselves.
 *
 * This is the client half of the generator handshake. The SurfaceSpec contract
 * licenses ten archetypes; five have no renderer here and collapse to the
 * tier-3 generic view. `implemented_archetypes.json` in
 * `packages/service-contracts` publishes this same set to the Python side,
 * where it — and not the ten-value contract enum — licenses the spec
 * generator. `licensedArchetypes.test.ts` pins the two together, so adding or
 * deleting an adapter fails until that shared file agrees, and updating that
 * file is the only edit needed to relicense the generator.
 */
export const IMPLEMENTED_ARCHETYPES: readonly string[] = ARCHETYPE_ADAPTERS.map(
  (adapter) => adapter.scheme,
);

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

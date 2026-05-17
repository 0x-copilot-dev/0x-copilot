// Web wrapper around the substrate-agnostic OrdinalCitationChip in
// @enterprise-search/chat-surface.
//
// The headless chip is a pure renderer; this wrapper owns the
// web-substrate-specific bits:
//
//   1. Resolves the ordinal against the active-run link registry via
//      the existing `useResolvedOrdinalCitation` hook.
//   2. Emits a debug breadcrumb when the resolution state changes —
//      web wires `citationDebug` (a console logger); a desktop wrapper
//      would wire the extension's telemetry sink instead.
//
// The desktop substrate will write a parallel wrapper that resolves
// against its bridge and chooses its own diagnostics. The chat-surface
// chip itself doesn't change.

import { OrdinalCitationChip as HeadlessOrdinalCitationChip } from "@enterprise-search/chat-surface";
import type { ReactElement } from "react";
import { useEffect, useRef } from "react";

import { citationDebug } from "../../chatModel/citationDebug";
import { useResolvedOrdinalCitation } from "./citationsContext";

export interface OrdinalCitationChipProps {
  conversationOrdinal: number;
  onSelect?: (ordinal: number, sourceToolCallId: string) => void;
}

export function OrdinalCitationChip({
  conversationOrdinal,
  onSelect,
}: OrdinalCitationChipProps): ReactElement {
  const resolved = useResolvedOrdinalCitation(conversationOrdinal);
  // Trace each chip's resolution outcome once per ordinal+state — first
  // mount and any subsequent state change. Helps diagnose "chip rendered
  // but unresolved" vs "chip never rendered".
  const lastLoggedRef = useRef<string | null>(null);
  useEffect(() => {
    const state = resolved === null ? "unresolved" : "resolved";
    const key = `${conversationOrdinal}:${state}`;
    if (lastLoggedRef.current !== key) {
      lastLoggedRef.current = key;
      citationDebug(
        `chip.${state} ordinal=${conversationOrdinal}` +
          (resolved ? ` call_id='${resolved.callId}'` : ""),
      );
    }
  }, [conversationOrdinal, resolved]);
  return (
    <HeadlessOrdinalCitationChip
      conversationOrdinal={conversationOrdinal}
      resolved={resolved}
      onSelect={onSelect}
    />
  );
}

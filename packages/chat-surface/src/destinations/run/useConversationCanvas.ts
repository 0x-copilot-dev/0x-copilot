// Conversation-scoped canvas subjects (PRD-02, GS-ARCH-05).
//
// The canvas answers two questions that one run-scoped projection was serving:
//
//   what can I open?      → conversation-scoped (this hook)
//   what is this run doing? → run-scoped (`projectCanvasLifecycle`)
//
// Conflating them is why a chat-only follow-up wiped an open surface:
// `useRunSession` clears `events` whenever `activeRunId` changes, so an artifact
// published two turns ago simply stopped existing as far as the canvas knew.
//
// Deliberately shaped after `useConversationSubagentArchive`, which already
// solves this for the Agents tab: seed from a conversation-scoped endpoint,
// remember live subjects, let the live stream win on conflict, and reset on
// CONVERSATION change — never on run change. Matching it means one pattern to
// understand rather than two that drift.

import { useEffect, useMemo, useState } from "react";

import type { ConversationId } from "@0x-copilot/api-types";

import { useTransport } from "../../providers/TransportProvider";

/** One openable subject, keyed identically to the client fold's subject key. */
export interface ConversationCanvasSubject {
  readonly subjectKey: string;
  readonly kind: "artifact" | "surface";
  readonly subjectId: string;
  /** Which run produced it. Provenance for display and for gating decisions. */
  readonly runId: string;
  readonly title: string;
  readonly revision: number | null;
  readonly rendererHint: string;
  readonly createdAt: string;
}

export interface ConversationCanvas {
  readonly subjects: readonly ConversationCanvasSubject[];
  readonly loading: boolean;
  readonly error: string | null;
}

interface WireSubject {
  readonly subject_key?: unknown;
  readonly kind?: unknown;
  readonly subject_id?: unknown;
  readonly run_id?: unknown;
  readonly title?: unknown;
  readonly revision?: unknown;
  readonly renderer_hint?: unknown;
  readonly created_at?: unknown;
}

const EMPTY: readonly ConversationCanvasSubject[] = [];

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}

/** Parse one wire subject, discarding anything malformed rather than guessing. */
function parseSubject(raw: WireSubject): ConversationCanvasSubject | null {
  const subjectKey = text(raw.subject_key);
  const subjectId = text(raw.subject_id);
  const runId = text(raw.run_id);
  const rendererHint = text(raw.renderer_hint);
  const kind = text(raw.kind);
  if (
    subjectKey === null ||
    subjectId === null ||
    runId === null ||
    rendererHint === null ||
    (kind !== "artifact" && kind !== "surface")
  ) {
    return null;
  }
  return {
    subjectKey,
    kind,
    subjectId,
    runId,
    // Titles are model-authored; the renderer escapes them, and an absent title
    // must not collapse the tab into a blank strip.
    title: text(raw.title) ?? "Untitled",
    revision:
      typeof raw.revision === "number" && Number.isSafeInteger(raw.revision)
        ? raw.revision
        : null,
    rendererHint,
    createdAt: text(raw.created_at) ?? "",
  };
}

/**
 * Subjects openable in this conversation, merged with what the live run stream
 * has already shown.
 *
 * `liveSubjects` wins on `subjectKey`: the current stream is fresher than an
 * archive fetched before this turn started, so a revision bump lands in place
 * instead of duplicating the tab.
 */
export function useConversationCanvas(
  conversationId: ConversationId,
  liveSubjects: readonly ConversationCanvasSubject[],
  enabled: boolean,
): ConversationCanvas {
  const transport = useTransport();
  const [archived, setArchived] =
    useState<readonly ConversationCanvasSubject[]>(EMPTY);
  const [remembered, setRemembered] = useState<
    ReadonlyMap<string, ConversationCanvasSubject>
  >(new Map());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || conversationId === "new") {
      setArchived(EMPTY);
      setRemembered(new Map());
      setLoading(false);
      setError(null);
      return undefined;
    }
    let cancelled = false;
    setArchived(EMPTY);
    setRemembered(new Map());
    setLoading(true);
    setError(null);
    void transport
      .request<{ subjects?: readonly WireSubject[] }>({
        method: "GET",
        path: `/v1/agent/conversations/${encodeURIComponent(conversationId)}/canvas`,
      })
      .then((response) => {
        if (cancelled) return;
        const parsed = Array.isArray(response?.subjects)
          ? response.subjects
              .map(parseSubject)
              .filter((s): s is ConversationCanvasSubject => s !== null)
          : [];
        setArchived(parsed);
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        // Hydration failure must recover in place (GS-ARCH-05): the error is
        // surfaced, but nothing already on screen is dropped because a
        // background read failed.
        setError(
          reason instanceof Error && reason.message !== ""
            ? reason.message
            : "Could not load earlier results.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // Keyed on the CONVERSATION. Re-fetching per run is exactly the coupling
    // this hook exists to remove.
  }, [conversationId, transport, enabled]);

  useEffect(() => {
    if (!enabled || liveSubjects.length === 0) return;
    setRemembered((previous) => {
      const next = new Map(previous);
      let changed = false;
      for (const subject of liveSubjects) {
        if (next.get(subject.subjectKey) !== subject) {
          next.set(subject.subjectKey, subject);
          changed = true;
        }
      }
      return changed ? next : previous;
    });
  }, [liveSubjects, enabled]);

  const subjects = useMemo(() => {
    const merged = new Map<string, ConversationCanvasSubject>();
    for (const subject of archived) merged.set(subject.subjectKey, subject);
    // Live wins — a subject seen on this run's stream is fresher than the
    // archive snapshot taken when the conversation was opened.
    for (const [key, subject] of remembered) merged.set(key, subject);
    return [...merged.values()].sort(
      (left, right) =>
        left.createdAt.localeCompare(right.createdAt) ||
        left.subjectKey.localeCompare(right.subjectKey),
    );
  }, [archived, remembered]);

  return { subjects, loading, error };
}

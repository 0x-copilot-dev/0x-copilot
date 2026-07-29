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

import { useEffect, useMemo, useRef, useState } from "react";

import type { ConversationId } from "@0x-copilot/api-types";

import { useTransport } from "../../providers/TransportProvider";
import { isSurfaceHue, type SurfaceHue } from "../../surfaces/surfaceHue";

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
  /**
   * The identity hue chosen at publication, if one was. `null` means no
   * preference — the tab derives a hue from the surface URI — which is a
   * different fact from the explicit `"none"` choice, meaning "show no
   * identity". Collapsing the two would make an author's deliberate decision
   * indistinguishable from silence.
   */
  readonly accent: SurfaceHue | null;
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
  readonly accent?: unknown;
  readonly created_at?: unknown;
}

const EMPTY: readonly ConversationCanvasSubject[] = [];

/**
 * What `parseSubject` substitutes when the wire carries no title. Named so the
 * merge can tell "the record has a title" from "the parser filled a hole" —
 * otherwise a titleless record would beat the fold's `"<kind> artifact"`, which
 * at least says what the thing IS.
 */
const UNTITLED = "Untitled";

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
    title: text(raw.title) ?? UNTITLED,
    revision:
      typeof raw.revision === "number" && Number.isSafeInteger(raw.revision)
        ? raw.revision
        : null,
    rendererHint,
    // Validated against the closed set rather than passed through: this value
    // becomes a `data-surface-hue` attribute, so an unrecognised string is
    // dropped to `null` and the URI-derived default applies.
    accent: isSurfaceHue(raw.accent) ? raw.accent : null,
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

  // Bumped when the live stream shows a subject the archive has never heard of.
  //
  // Without this the archive is fetched exactly once, when the conversation
  // binds — which is BEFORE the run publishes anything. So for the entire run
  // that produced an artifact, `archived` stayed empty, the live subject won
  // wholesale, and everything only the RECORD knows was lost: the tab showed
  // "dataset artifact · r1" instead of "forecast.csv", and a chosen accent never
  // appeared at all. Both came back on the next reload, which is why the defect
  // reads as "it works when I check it later".
  const [refreshToken, setRefreshToken] = useState(0);
  // Keys we have already refetched for. A subject can be legitimately absent
  // from the archive forever — `publish_artifact` may decide chat-card or
  // hidden, and only canvas-decided artifacts are returned — so retrying per
  // render would spin. One attempt per key is enough to pick up a real one.
  const requested = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!enabled || conversationId === "new") {
      setArchived(EMPTY);
      setRemembered(new Map());
      setLoading(false);
      setError(null);
      requested.current = new Set();
      return undefined;
    }
    let cancelled = false;
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
    // Keyed on the CONVERSATION, plus a refresh for subjects the archive has
    // not seen. Still never keyed on the RUN — that coupling is what this hook
    // exists to remove; a refresh is triggered by a new SUBJECT, not a new run.
  }, [conversationId, transport, enabled, refreshToken]);

  useEffect(() => {
    if (!enabled) return;
    const known = new Set(archived.map((subject) => subject.subjectKey));
    let unseen = false;
    for (const subject of liveSubjects) {
      if (known.has(subject.subjectKey)) continue;
      if (requested.current.has(subject.subjectKey)) continue;
      requested.current.add(subject.subjectKey);
      unseen = true;
    }
    if (unseen) setRefreshToken((token) => token + 1);
  }, [liveSubjects, archived, enabled]);

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
    //
    // But only for facts the run fold can actually observe. `accent` is not one
    // of them: `artifact.created` carries no accent, so the fold's value is a
    // hardcoded `null` — silence, not a choice. Replacing the whole archived
    // subject therefore ERASED every author-chosen accent the moment the
    // publishing run's own events were folded, which is the ordinary case:
    // publish an artifact, look at its tab, and the colour you asked for was
    // already gone. Whole-object replacement is safe for a field the fold
    // populates (title degrades) and destructive for one it cannot (accent).
    //
    // `title` has exactly the same problem, and it is visible: the fold can only
    // synthesize "<kind> artifact" because `artifact.created` carries no title
    // either, so a published forecast.csv showed "dataset artifact · r1" on its
    // tab while the surface header right beside it said "forecast.csv". PRD-02
    // fixed the tab to read the record's title — and this merge then threw that
    // record away before the tab ever saw it.
    //
    // The rule, stated once: the fold wins for what it OBSERVES (revision, run),
    // and the record wins for what the fold can only guess at.
    for (const [key, subject] of remembered) {
      const stored = merged.get(key);
      merged.set(
        key,
        stored === undefined
          ? subject
          : {
              ...subject,
              title: stored.title === UNTITLED ? subject.title : stored.title,
              accent: subject.accent ?? stored.accent,
            },
      );
    }
    return [...merged.values()].sort(
      (left, right) =>
        left.createdAt.localeCompare(right.createdAt) ||
        left.subjectKey.localeCompare(right.subjectKey),
    );
  }, [archived, remembered]);

  return { subjects, loading, error };
}

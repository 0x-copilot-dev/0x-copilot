import { Button } from "@0x-copilot/design-system";
import { useEffect, useMemo, useRef, useState, type ReactElement } from "react";

// PR 1.4.1 Gap #10 — workspace member picker for the "Approve & forward
// to…" flow.
//
// The picker is decoupled from its data source: callers pass in a
// ``loadMembers(query)`` function. The default impl in PR 1.4.1 Phase C
// is a stub that returns the literal user_id the user typed (matching
// PR 1.4 free-text behavior); the proper backend-backed loader lands
// when the ``GET /v1/workspace/members?q=`` endpoint ships in a tightly
// scoped follow-up.
//
// Server-side validation (PR 1.4.1 Gap #1's
// ``WorkspaceMembershipResolver``) is the ground truth — even when the
// picker accepts free-text fall-throughs, the API rejects unknown /
// inactive / cross-org targets with 422 before any DB write. The
// picker is a UX improvement, not a security boundary.
//
// DRY anchor: this component will become a wrapper around the
// ``useWorkspaceMembers`` hook once the @-mention picker lands as part
// of W3.1; both consume the same hook + endpoint. No fork.

export interface WorkspaceMember {
  user_id: string;
  display_name: string;
  email?: string | null;
}

export type WorkspaceMemberLoader = (
  query: string,
) => Promise<readonly WorkspaceMember[]>;

/**
 * Default loader: passes the typed query through as a synthetic
 * ``user_id``-only member. The ``display_name`` matches the typed
 * string so the picker still renders a meaningful row. Once the
 * backend route lands, swap this for an HTTP-backed loader that hits
 * ``GET /v1/workspace/members?q=``.
 */
export const passthroughMemberLoader: WorkspaceMemberLoader = async (query) => {
  const trimmed = query.trim();
  if (trimmed.length === 0) {
    return [];
  }
  return [
    {
      user_id: trimmed,
      display_name: trimmed,
    },
  ];
};

/**
 * Debounced typeahead picker. Submitting (Enter / click) with no
 * matching member is a no-op; Escape cancels.
 */
export function WorkspaceMemberPicker({
  loadMembers = passthroughMemberLoader,
  excludeUserIds,
  onPick,
  onCancel,
  debounceMs = 200,
  placeholder = "user_id (e.g. marcus)",
}: {
  loadMembers?: WorkspaceMemberLoader;
  excludeUserIds?: readonly string[];
  onPick: (member: WorkspaceMember) => void;
  onCancel: () => void;
  debounceMs?: number;
  placeholder?: string;
}): ReactElement {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<readonly WorkspaceMember[]>([]);
  const [highlighted, setHighlighted] = useState(0);
  const exclude = useMemo(
    () => new Set(excludeUserIds ?? []),
    [excludeUserIds],
  );
  const lastQueryRef = useRef("");

  useEffect(() => {
    const trimmed = query.trim();
    lastQueryRef.current = trimmed;
    if (trimmed.length === 0) {
      setResults([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void loadMembers(trimmed).then((members) => {
        if (cancelled || lastQueryRef.current !== trimmed) {
          return;
        }
        const filtered = members.filter(
          (member) => !exclude.has(member.user_id),
        );
        setResults(filtered);
        setHighlighted(0);
      });
    }, debounceMs);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [query, loadMembers, exclude, debounceMs]);

  const submit = (): void => {
    const candidate = results[highlighted];
    if (candidate) {
      onPick(candidate);
    }
  };

  return (
    <div className="aui-tool-card__forward-picker">
      <input
        type="text"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder={placeholder}
        aria-label="Forward to workspace member"
        autoFocus
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            submit();
            return;
          }
          if (event.key === "Escape") {
            event.preventDefault();
            onCancel();
            return;
          }
          if (event.key === "ArrowDown") {
            event.preventDefault();
            setHighlighted((current) =>
              Math.min(current + 1, Math.max(0, results.length - 1)),
            );
            return;
          }
          if (event.key === "ArrowUp") {
            event.preventDefault();
            setHighlighted((current) => Math.max(current - 1, 0));
          }
        }}
      />
      <Button
        type="button"
        size="sm"
        title="Forward this decision"
        onClick={submit}
        disabled={results.length === 0}
      >
        Forward
      </Button>
      <Button
        type="button"
        size="sm"
        variant="secondary"
        title="Cancel forwarding"
        onClick={onCancel}
      >
        Cancel
      </Button>
      {results.length > 0 ? (
        <ul
          className="aui-tool-card__forward-picker-results"
          role="listbox"
          aria-label="Workspace members"
        >
          {results.map((member, index) => (
            <li
              key={member.user_id}
              role="option"
              aria-selected={index === highlighted}
              data-highlighted={index === highlighted ? "true" : undefined}
            >
              <button
                type="button"
                onClick={() => onPick(member)}
                onMouseEnter={() => setHighlighted(index)}
                className="aui-tool-card__forward-picker-result"
              >
                <span className="aui-tool-card__forward-picker-result-name">
                  {member.display_name}
                </span>
                <span className="aui-tool-card__forward-picker-result-id">
                  {member.email ?? member.user_id}
                </span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

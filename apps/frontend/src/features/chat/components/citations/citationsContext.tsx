// PR 1.1 — React context that exposes the run-scoped citation registry to
// the markdown chip resolver. The reducer state is owned by ChatScreen;
// this context is just the read seam used by `CitationChip`.

import {
  createContext,
  useContext,
  useMemo,
  type ReactElement,
  type ReactNode,
} from "react";
import type { CitationSourceRef } from "@enterprise-search/api-types";

export type CitationLookup = ReadonlyMap<string, CitationSourceRef>;

const EMPTY_LOOKUP: CitationLookup = new Map();
const CitationsContext = createContext<CitationLookup>(EMPTY_LOOKUP);

export function CitationsProvider({
  citations,
  children,
}: {
  citations: CitationLookup;
  children: ReactNode;
}): ReactElement {
  // Memoize so consumers don't re-render when the parent re-creates the
  // map identity but its contents are unchanged.
  const value = useMemo(() => citations, [citations]);
  return (
    <CitationsContext.Provider value={value}>
      {children}
    </CitationsContext.Provider>
  );
}

export function useCitation(citationId: string): CitationSourceRef | undefined {
  return useContext(CitationsContext).get(citationId);
}

export function useCitations(): CitationLookup {
  return useContext(CitationsContext);
}

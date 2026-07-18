// Re-export shim for the run-scoped citation read context.
//
// The context + hooks now live in @0x-copilot/chat-surface (PR-1.4) so web
// and desktop resolve citation chips against the same registries. The module
// has no substrate-specific dependency (pure React context over the hoisted
// citation + link registries), so this is a pure re-export rather than a host
// adapter; existing import sites keep resolving `CitationsProvider` /
// `useCitation` / `useRunCitations` / `useResolvedOrdinalCitation` /
// `useOrdinalCitation` from here.

export {
  CitationsProvider,
  useCitation,
  useRunCitations,
  useResolvedOrdinalCitation,
  useOrdinalCitation,
  type CitationLookup,
  type CitationsProviderProps,
  type ResolvedOrdinalCitation,
} from "@0x-copilot/chat-surface";

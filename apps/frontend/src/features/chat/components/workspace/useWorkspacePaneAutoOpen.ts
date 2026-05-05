// PR 1.5 — workspace pane auto-open trigger.
//
// The pane defaults closed, then auto-opens on the first conversation
// switch where any of the four content tabs has non-zero data. Once the
// user manually toggles the pane, their preference wins; this hook only
// computes whether *the initial state* on conversation switch should be
// open. ChatScreen wires it to a one-shot effect keyed by conversationId.

export function shouldAutoOpenWorkspacePane(opts: {
  subagentCount: number;
  sourceCount: number;
  draftCount?: number;
  pendingApprovalsCount?: number;
}): boolean {
  return (
    opts.subagentCount > 0 ||
    opts.sourceCount > 0 ||
    (opts.draftCount ?? 0) > 0 ||
    (opts.pendingApprovalsCount ?? 0) > 0
  );
}

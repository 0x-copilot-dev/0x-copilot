export interface EmailFixtureDraft {
  readonly draftId: string;
  readonly to: string;
  readonly cc: string;
  readonly subject: string;
  readonly bodyPrefix: string;
  readonly bodySuffix: string;
}

export interface EmailFixturePendingDiff {
  readonly diffId: string;
  readonly provenance: string;
  readonly title: string;
  readonly description: string;
  readonly regionAnchorId: string;
}

export interface EmailFixture {
  readonly draft: EmailFixtureDraft;
  readonly streamingBodyChunks: readonly string[];
  readonly pendingDiff: EmailFixturePendingDiff;
}

export const EMAIL_FIXTURE: EmailFixture = {
  draft: {
    draftId: "draft-1",
    to: "jordan.reyes@acme.com",
    cc: "s.park@yourco.com",
    subject: "Renewal terms — Q4 wrap and FY27 path",
    bodyPrefix: "Hi Jordan,\n\n",
    bodySuffix:
      "\n\nLet me know if you'd like to walk through the deltas tomorrow.\n\nBest,\nSam",
  },
  streamingBodyChunks: [
    "Confirming the locked-price block from MSA §3.2: ",
    "for the Q4 wrap, the per-seat rate stays at $84 with the agreed 7% volume credit. ",
    "For the FY27 path, we're proposing a 4% uplift in Q1, ",
    "capped at the lesser of CPI or 4.5%, ",
    "with a 30-day notice window before renewal.",
  ],
  pendingDiff: {
    diffId: "diff-1",
    provenance: "DRAFTED FROM SALESFORCE + Q4 SHEET",
    title: "Locked-price block sourced from MSA §3.2.",
    description: "Approve to send when ready.",
    regionAnchorId: "pending-block",
  },
};

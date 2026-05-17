import type { TcInlineDiffProps } from "./TcInlineDiff";

export interface InlineDiffFixture {
  readonly label: string;
  readonly props: TcInlineDiffProps;
}

const noop = (): void => undefined;

export const inlineDiffFixtures: readonly InlineDiffFixture[] = [
  {
    label: "idle",
    props: {
      state: "idle",
      title: "No proposed change",
      description: "Waiting for the agent to begin.",
    },
  },
  {
    label: "streaming (indeterminate)",
    props: {
      state: "streaming",
      title: "Drafting an email reply",
      description: "Composing tone-matched response from thread context.",
    },
  },
  {
    label: "streaming (determinate 64%)",
    props: {
      state: "streaming",
      progressPercent: 64,
      title: "Drafting an email reply",
      description: "Composing tone-matched response from thread context.",
    },
  },
  {
    label: "pending (no provenance)",
    props: {
      state: "pending",
      title: "Reply to Maria with proposed times",
      description: "Two windows on Thursday, one on Friday.",
      onApprove: noop,
      onReject: noop,
    },
  },
  {
    label: "pending (with provenance)",
    props: {
      state: "pending",
      provenance: "DRAFTED FROM SALESFORCE",
      title: "Update opportunity stage to Closed-Won",
      description: "Stage: Negotiation → Closed-Won; ACV: $48,000.",
      onApprove: noop,
      onReject: noop,
    },
  },
  {
    label: "pending (with suggest-changes)",
    props: {
      state: "pending",
      provenance: "DRAFTED FROM GMAIL",
      title: "Reply to Maria with proposed times",
      description: "Two windows on Thursday, one on Friday.",
      onApprove: noop,
      onReject: noop,
      onSuggestChanges: noop,
    },
  },
  {
    label: "accepted",
    props: {
      state: "accepted",
      title: "Email sent to Maria",
      description: "Delivered 2 minutes ago.",
    },
  },
  {
    label: "rejected",
    props: {
      state: "rejected",
      title: "Discarded the proposed reply",
      description: "Reverted to draft. No external action taken.",
    },
  },
];

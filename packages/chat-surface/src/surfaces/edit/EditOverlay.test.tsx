// PRD-09c — edit-on-surface overlay unit tests.
//
// Covers the pure derivation the overlay owns (D28 host-side edit UI):
//   - `seedEdits` derives the initial `SurfaceEdits` from the proposal
//     (message → body + every changed hunk id; record → per-field values);
//   - MessageEditForm seeds the body textarea from the proposal, toggling a
//     PRD-06 hunk excludes it from `accepted_hunk_ids`, and the edited body +
//     kept-hunk set flow up on submit;
//   - RecordEditForm seeds one input per changed field and lifts edits;
//   - Cancel reports nothing.
//
// The overlay never fetches and touches no substrate global — it takes a
// `SurfaceDiff` + callbacks and hands back a `SurfaceEdits`.

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { SurfaceDiff } from "@0x-copilot/api-types";

import { EditOverlay, seedEdits } from "./EditOverlay";

const MESSAGE_DIFF: SurfaceDiff = {
  spec: {
    spec_version: 1,
    archetype: "message",
    source: { server: "seed:gmail", tool: "send" },
    title_path: "message.subject",
    fields: [
      { label: "Subject", path: "message.subject" },
      { label: "Body", path: "message.body" },
    ],
  },
  changes: [
    { field: "message.subject", old: "Renewal", new: "Renewal terms" },
    {
      field: "message.body",
      old: "Hi Jordan, the price holds.",
      new: "Hi Maya, the price holds.",
    },
  ],
};

const RECORD_DIFF: SurfaceDiff = {
  changes: [
    { field: "title", old: "Old title", new: "New title" },
    { field: "priority", old: "P2", new: "P1" },
  ],
};

describe("seedEdits", () => {
  it("seeds the message body + every changed hunk id from the proposal", () => {
    const edits = seedEdits("message", MESSAGE_DIFF);
    expect(edits.body).toBe("Hi Maya, the price holds.");
    // wordDiff("Hi Jordan, the price holds.", "Hi Maya, the price holds.")
    // → equal(h0) · delete(h1) · insert(h2) · equal(h3); changed = h1, h2.
    expect(edits.accepted_hunk_ids).toEqual(["h1", "h2"]);
    expect(edits.fields).toBeUndefined();
  });

  it("seeds record fields with the proposed values", () => {
    const edits = seedEdits("record", RECORD_DIFF);
    expect(edits.fields).toEqual({ title: "New title", priority: "P1" });
    expect(edits.body).toBeUndefined();
    expect(edits.accepted_hunk_ids).toBeUndefined();
  });
});

describe("EditOverlay — message archetype", () => {
  it("seeds the body textarea, shows subject read-only, and derives edits on submit", () => {
    const onSubmit = vi.fn();
    const onCancel = vi.fn();
    render(
      <EditOverlay
        archetype="message"
        diff={MESSAGE_DIFF}
        title="Send the renewal email"
        onSubmit={onSubmit}
        onCancel={onCancel}
      />,
    );

    // Body <textarea> seeded from the proposed body.
    const body = screen.getByTestId("message-edit-body") as HTMLTextAreaElement;
    expect(body.value).toBe("Hi Maya, the price holds.");
    // Subject is a read-only meta row (v1 does not edit to/subject).
    const subject = screen.getByTestId(
      "message-edit-meta-message.subject",
    ) as HTMLInputElement;
    expect(subject.value).toBe("Renewal terms");
    expect(subject.readOnly).toBe(true);

    // Edit the body, then submit.
    fireEvent.change(body, { target: { value: "Hi Maya, price is locked." } });
    fireEvent.click(screen.getByTestId("surface-edit-submit"));

    expect(onCancel).not.toHaveBeenCalled();
    expect(onSubmit).toHaveBeenCalledTimes(1);
    const edits = onSubmit.mock.calls[0][0];
    expect(edits.body).toBe("Hi Maya, price is locked.");
    // No hunk toggled → the full proposed hunk set is kept.
    expect(edits.accepted_hunk_ids).toEqual(["h1", "h2"]);
  });

  it("toggling a PRD-06 hunk excludes it from accepted_hunk_ids", () => {
    const onSubmit = vi.fn();
    render(
      <EditOverlay
        archetype="message"
        diff={MESSAGE_DIFF}
        onSubmit={onSubmit}
        onCancel={vi.fn()}
      />,
    );

    // Click the inserted hunk (h2) in the DiffText → excludes it.
    const hunks = screen.getByTestId("message-edit-hunks");
    fireEvent.click(within(hunks).getByTestId("diff-insert"));

    // The read-only status mirror reflects the exclusion…
    expect(
      screen
        .getByTestId("message-edit-hunk-status-h2")
        .getAttribute("data-accepted"),
    ).toBe("false");
    expect(
      screen
        .getByTestId("message-edit-hunk-status-h1")
        .getAttribute("data-accepted"),
    ).toBe("true");

    // …and submit carries only the kept hunk.
    fireEvent.click(screen.getByTestId("surface-edit-submit"));
    expect(onSubmit.mock.calls[0][0].accepted_hunk_ids).toEqual(["h1"]);
  });
});

describe("EditOverlay — record archetype", () => {
  it("seeds one input per changed field and derives fields on submit", () => {
    const onSubmit = vi.fn();
    render(
      <EditOverlay
        archetype="record"
        diff={RECORD_DIFF}
        onSubmit={onSubmit}
        onCancel={vi.fn()}
      />,
    );

    const title = screen.getByTestId(
      "record-edit-field-title",
    ) as HTMLInputElement;
    const priority = screen.getByTestId(
      "record-edit-field-priority",
    ) as HTMLInputElement;
    expect(title.value).toBe("New title");
    expect(priority.value).toBe("P1");

    fireEvent.change(title, { target: { value: "Reviewed title" } });
    fireEvent.click(screen.getByTestId("surface-edit-submit"));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0].fields).toEqual({
      title: "Reviewed title",
      priority: "P1",
    });
  });
});

describe("EditOverlay — cancel", () => {
  it("reports cancel without submitting", () => {
    const onSubmit = vi.fn();
    const onCancel = vi.fn();
    render(
      <EditOverlay
        archetype="record"
        diff={RECORD_DIFF}
        onSubmit={onSubmit}
        onCancel={onCancel}
      />,
    );

    fireEvent.click(screen.getByTestId("surface-edit-cancel"));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });
});

# surface-colour — the Studio's identity colour, in the real app

**User story.** "The app has no colours — it's only black, grey and white."

A user publishes a CSV, looks at Studio, and every surface is the same grey
regardless of what it is or where it came from. Two tabs open side by side are
visually indistinguishable, and a table of numbers is a wall of digits with no
sense of scale.

## Why this journey exists rather than a unit test

The colour system spans four layers that only meet in the running app:

1. design-system tokens (`--surface-hue-*`, resolved by theme),
2. a package stylesheet (`surface-language.css`) that **both hosts must import**,
3. renderer markup that composes **inline styles**, and
4. a backend `accent` that travels record → canvas endpoint → tab.

Every one of those has already produced a defect that unit tests passed through:

- `.sf-col--numeric` was completely **inert**, because `thStyle` emits `color`
  inline and an inline declaration outranks any stylesheet rule. The unit test
  asserted `toHaveClass(...)` and passed while the rule did nothing.
- A hand-built verification page rendered the same `<th>` **without** that inline
  colour and reported the hue working. The markup has to be the app's own.
- The published `accent` never reached its own tab: the live/archived subject
  merge overwrote it with the run fold's hardcoded `null`.
- `DatasetArtifactRenderer` — the surface a published CSV actually uses — had a
  second `<table>` that shared none of the table language.

A green run here means the colour is on screen in the shipped app, measured by
`getComputedStyle`, not asserted against a fixture.

## Steps

1. Sign in, add a BYOK key, ask the agent to publish a CSV (reuses G2's prompt).
2. Wait for the run to seal and the canvas to auto-present the dataset.
3. Read computed styles out of the live DOM.

## What it asserts

| #   | Claim                                                                                              |
| --- | -------------------------------------------------------------------------------------------------- |
| 1   | The canvas tab carries `data-surface-hue="sky"` for `artifact-dataset://`                          |
| 2   | The tab dot's computed colour is a real hue, **not** a neutral grey                                |
| 3   | The surface mount carries the same hue, so tab and card cannot disagree                            |
| 4   | A numeric column header's computed colour **differs** from a text header's — the inert-rule defect |
| 5   | Value bars exist behind numeric cells, are `aria-hidden`, and are ordered by magnitude             |
| 6   | The conversation-canvas wire carries the `accent` field, and the tab honours it when set           |

Claim 4 is the one that matters most: it is the only assertion that would have
caught the inline-style defect, and it can only be made against real markup.

## What blocks fuller coverage

- The model chooses `accent` at its discretion, so claim 6 asserts the **wire
  contract and the tab's agreement with it**, not that a particular colour was
  chosen. Forcing a colour would test the prompt, not the system.
- Light-theme hues are verified by computed style in `tools/design-parity`; the
  desktop app ships dark, so this journey measures the dark ring only.

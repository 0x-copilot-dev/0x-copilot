# Installed npm payload — J1 smoke

**User story.** A user has run `make desktop-install`. The journey must drive
the exact global `@0x-copilot/cli` npm package they received, not the checkout
that built it.

| Step | User action                                 | Required proof                                                                                                                    |
| ---- | ------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| J1   | Launch the app through the journey harness. | Driver status reports `installed-payload`; `appDir` ends in `@0x-copilot/cli/payload/desktop`; no `APP_DIR` override is accepted. |
| J2   | Observe the initial gate.                   | The real production sign-in gate appears and a screenshot is recorded.                                                            |

This is deliberately a small, keyless smoke. Run the richer journey suites with
the same target to verify the installed artifact's chat cards and interactions.

# design-kit — shared Claude Design source

The tokens/base/kit-stubs every surface harness links. Vendored from the Claude
Design project so the parity harness is self-contained and version-controlled
(no live DesignSync dependency at run time).

| File          | What it is                                                                                                                                                                                        | Source (DesignSync)                              |
| ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| `copilot.css` | v2 "quiet" tokens (`:root` --tx/--accent/--jade/…), base element styles, window chrome (`.mw`), **login** (`.login-*`/`.mrow`/`.sig-*`/`.cbtn`/`.empty`), `.spin`                                 | `copilot.css`                                    |
| `stubs.js`    | Minimal kit globals for a parity render — `Icon` (Proxy → generic 1em glyph), `Mark`, `useTweaks` (`?state=` override). Icons are sized by CSS, so geometry is irrelevant to a CSS-parity render. | replaces `copilot-data.jsx` + `tweaks-panel.jsx` |

**Not yet vendored (fetch when a surface needs it):**

| File               | Needed by                                                                           | Source                                         |
| ------------------ | ----------------------------------------------------------------------------------- | ---------------------------------------------- |
| `copilot-v3.css`   | first-run **composer** state, the run cockpit — `.cmp*` composer + `.pop*` popovers | `copilot-v3.css` (it `@import`s `copilot.css`) |
| `copilot-data.jsx` | pixel-faithful icons (not needed for CSS parity — stubs suffice)                    | `copilot-data.jsx`                             |

## How to refresh (or add a surface's source)

Use the **DesignSync** tool (auth via `/design-login`). Project:

- name: **Copilot**
- projectId: **`73f810d9-7b77-4849-9087-f7f8e366c48a`**

```
DesignSync list_files  projectId=73f810d9-7b77-4849-9087-f7f8e366c48a
DesignSync get_file     projectId=… path="copilot.css"            # → design-kit/copilot.css
DesignSync get_file     projectId=… path="<surface>.jsx/.css"     # → surfaces/<name>/design/
```

Surface source files seen in the project (2026-07-22): `copilot-login.jsx`,
`copilot-firstrun.jsx` + `copilot-firstrun.css`, `copilot-settings.jsx` +
`settings.css`, `copilot-loading.jsx`, `copilot-workspace3.jsx`,
`copilot-run-side.jsx`, `composer.jsx`, plus rendered `0xCopilot *.html` exports.

**Keep `copilot.css` byte-faithful.** Prettier will reformat whitespace on commit
(that's fine — values are unchanged), but never hand-edit token VALUES to match the
app: the app is what moves toward this baseline, not the reverse.

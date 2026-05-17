// @vitest-environment node
import { describe, expect, it } from "vitest";

import { ALLOWED_IMPORTS, astAllowlistScan } from "./ast-allowlist";

function violationKinds(source: string): string[] {
  const result = astAllowlistScan(source);
  if (result.ok) return [];
  return result.violations.map((v) => v.kind);
}

describe("astAllowlistScan — ALLOWED_IMPORTS shape", () => {
  it("exposes the three modules", () => {
    expect(Object.keys(ALLOWED_IMPORTS)).toEqual([
      "react",
      "react-dom",
      "@enterprise-search/design-system",
    ]);
  });

  it("includes createElement / Fragment / useState in react", () => {
    expect(ALLOWED_IMPORTS.react).toContain("createElement");
    expect(ALLOWED_IMPORTS.react).toContain("Fragment");
    expect(ALLOWED_IMPORTS.react).toContain("useState");
  });

  it("explicitly excludes useEffect / useRef / useLayoutEffect from react (D28)", () => {
    expect(ALLOWED_IMPORTS.react).not.toContain("useEffect");
    expect(ALLOWED_IMPORTS.react).not.toContain("useRef");
    expect(ALLOWED_IMPORTS.react).not.toContain("useLayoutEffect");
  });

  it("excludes Menu / Popover / ThemeProvider from design-system", () => {
    const ds = ALLOWED_IMPORTS["@enterprise-search/design-system"];
    expect(ds).not.toContain("Menu");
    expect(ds).not.toContain("Popover");
    expect(ds).not.toContain("ThemeProvider");
    expect(ds).not.toContain("useTheme");
  });
});

describe("astAllowlistScan — allowed", () => {
  it("accepts a minimal adapter that imports only from the allowlist", () => {
    const source = `
      import { createElement, useState } from 'react';
      import { Button, Card } from '@enterprise-search/design-system';
      module.exports = {
        scheme: 'demo',
        matches: (uri) => uri.startsWith('demo://'),
        renderCurrent: (state) => createElement(Card, null, 'hi'),
        renderDiff: (diff) => createElement(Button, null, 'apply'),
        metadata: { origin: 'agent-generated', schemaVersion: 1 },
      };
    `;
    expect(astAllowlistScan(source)).toEqual({ ok: true });
  });

  it("accepts arrow functions, destructuring, and useState calls", () => {
    const source = `
      import { createElement, useState } from 'react';
      const Cell = ({ value }) => {
        const [open, setOpen] = useState(false);
        return createElement('div', { 'data-open': open }, value);
      };
      module.exports = {
        scheme: 'x',
        matches: () => true,
        renderCurrent: (s) => createElement(Cell, { value: s.value }),
        renderDiff: () => createElement('div'),
        metadata: { origin: 'agent-generated', schemaVersion: 1 },
      };
    `;
    expect(astAllowlistScan(source)).toEqual({ ok: true });
  });

  it("does not flag identifiers that are property keys named like forbidden globals", () => {
    const source = `
      import { createElement } from 'react';
      const obj = { process: 1, window: 2, fetch: 3 };
      const x = obj.process + obj.window + obj.fetch;
      module.exports = {
        scheme: 'x', matches: () => true,
        renderCurrent: () => createElement('div', null, String(x)),
        renderDiff: () => createElement('div'),
        metadata: { origin: 'agent-generated', schemaVersion: 1 },
      };
    `;
    expect(astAllowlistScan(source)).toEqual({ ok: true });
  });
});

describe("astAllowlistScan — disallowed imports", () => {
  it("rejects import from outside the allowlist (child_process)", () => {
    const result = astAllowlistScan(`import cp from 'child_process';`);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(
        result.violations.some((v) => v.kind === "import.disallowed-module"),
      ).toBe(true);
    }
  });

  it("rejects import from outside the allowlist (fs)", () => {
    expect(violationKinds(`import * as fs from 'fs';`)).toContain(
      "import.disallowed-module",
    );
  });

  it("rejects import { useEffect } from 'react' (D28)", () => {
    const result = astAllowlistScan(
      `import { useEffect } from 'react'; useEffect(() => {}, []);`,
    );
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(
        result.violations.some((v) => v.kind === "import.disallowed-specifier"),
      ).toBe(true);
    }
  });

  it("rejects default import from an allowed module", () => {
    expect(violationKinds(`import React from 'react';`)).toContain(
      "import.default-not-allowed",
    );
  });

  it("rejects namespace import from an allowed module", () => {
    expect(
      violationKinds(`import * as DS from '@enterprise-search/design-system';`),
    ).toContain("import.namespace-not-allowed");
  });

  it("rejects dynamic import()", () => {
    expect(violationKinds(`const m = import('react');`)).toContain(
      "dynamic-import",
    );
  });

  it("rejects require()", () => {
    expect(violationKinds(`const fs = require('fs');`)).toContain(
      "require-call",
    );
  });
});

describe("astAllowlistScan — forbidden globals", () => {
  it("rejects bare fetch()", () => {
    expect(violationKinds(`fetch('https://evil.example/');`)).toContain(
      "forbidden-global",
    );
  });

  it("rejects new XMLHttpRequest()", () => {
    expect(violationKinds(`const x = new XMLHttpRequest();`)).toContain(
      "forbidden-global",
    );
  });

  it("rejects window / document access", () => {
    expect(violationKinds(`const d = document;`)).toContain("forbidden-global");
    expect(violationKinds(`const w = window;`)).toContain("forbidden-global");
  });

  it("rejects process / globalThis references", () => {
    expect(violationKinds(`const p = process.env;`)).toContain(
      "forbidden-global",
    );
    expect(violationKinds(`const g = globalThis;`)).toContain(
      "forbidden-global",
    );
  });

  it("catches the aliased-fetch pattern at the reference site", () => {
    const kinds = violationKinds(`const f = fetch; f('x');`);
    expect(kinds).toContain("forbidden-global");
  });

  it("rejects WebSocket / EventSource", () => {
    expect(violationKinds(`new WebSocket('ws://x');`)).toContain(
      "forbidden-global",
    );
    expect(violationKinds(`new EventSource('/x');`)).toContain(
      "forbidden-global",
    );
  });
});

describe("astAllowlistScan — eval and Function", () => {
  it("rejects eval()", () => {
    expect(violationKinds(`eval('1+1');`)).toContain("eval-call");
  });

  it("rejects new Function(...)", () => {
    expect(violationKinds(`const f = new Function('return 1');`)).toContain(
      "function-constructor-new",
    );
  });

  it("rejects Function(...) without new", () => {
    expect(violationKinds(`const f = Function('return 1');`)).toContain(
      "function-constructor-call",
    );
  });
});

describe("astAllowlistScan — prototype pollution patterns", () => {
  it("rejects __proto__ access", () => {
    expect(violationKinds(`const p = ({}).__proto__;`)).toContain(
      "proto-access",
    );
  });

  it("rejects writing to .prototype", () => {
    expect(violationKinds(`function Foo() {} Foo.prototype = {};`)).toContain(
      "prototype-mutation",
    );
  });
});

describe("astAllowlistScan — parse errors", () => {
  it("reports a parse-error for syntactically invalid source", () => {
    const result = astAllowlistScan(`const = ;`);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.violations[0].kind).toBe("parse-error");
    }
  });
});

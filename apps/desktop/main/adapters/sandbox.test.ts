// @vitest-environment node
import { describe, expect, it } from "vitest";

import {
  compileAdapter,
  __createSandboxContextForTests,
  __probeForbiddenForTests,
} from "./sandbox";

const GOOD_ADAPTER_SOURCE = `
  module.exports = {
    scheme: 'demo',
    matches: function (uri) { return uri.indexOf('demo://') === 0; },
    renderCurrent: function (_state) {
      return { type: 'div', props: null, children: ['ok'] };
    },
    renderDiff: function (_diff) {
      return { type: 'div', props: null, children: ['diff'] };
    },
    metadata: { origin: 'agent-generated', schemaVersion: 1 },
  };
`;

describe("compileAdapter — success path", () => {
  it("returns the adapter object for a well-formed source", () => {
    const result = compileAdapter(GOOD_ADAPTER_SOURCE);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.adapter.scheme).toBe("demo");
      expect(result.adapter.metadata.origin).toBe("agent-generated");
      expect(result.adapter.metadata.schemaVersion).toBe(1);
      expect(typeof result.adapter.matches).toBe("function");
      expect(typeof result.adapter.renderCurrent).toBe("function");
      expect(typeof result.adapter.renderDiff).toBe("function");
      expect(result.adapter.matches("demo://x")).toBe(true);
      expect(result.adapter.matches("other://x")).toBe(false);
    }
  });
});

describe("compileAdapter — failure paths", () => {
  it("returns syntax error for invalid source", () => {
    const result = compileAdapter(`const = ;`);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toBe("syntax");
    }
  });

  it("returns runtime error when the source throws during evaluation", () => {
    const source = `throw new Error('boom-at-load');`;
    const result = compileAdapter(source);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toBe("runtime");
      expect(result.detail).toContain("boom-at-load");
    }
  });

  it("returns shape error when module.exports is missing fields", () => {
    const source = `module.exports = { scheme: 'demo' };`;
    const result = compileAdapter(source);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toBe("shape");
    }
  });

  it("returns shape error when metadata is wrong type", () => {
    const source = `
      module.exports = {
        scheme: 'x',
        matches: () => true,
        renderCurrent: () => null,
        renderDiff: () => null,
        metadata: { origin: 'agent-generated' },
      };
    `;
    const result = compileAdapter(source);
    expect(result.ok).toBe(false);
    if (!result.ok) {
      expect(result.reason).toBe("shape");
    }
  });

  it("accepts adapter assigned to exports.* style", () => {
    const source = `
      exports.scheme = 'demo';
      exports.matches = function () { return true; };
      exports.renderCurrent = function () { return null; };
      exports.renderDiff = function () { return null; };
      exports.metadata = { origin: 'agent-generated', schemaVersion: 2 };
    `;
    const result = compileAdapter(source);
    expect(result.ok).toBe(true);
    if (result.ok) {
      expect(result.adapter.metadata.schemaVersion).toBe(2);
    }
  });
});

describe("compileAdapter — privileged globals are absent in the sandbox", () => {
  const { context } = __createSandboxContextForTests();

  it("process is undefined", () => {
    expect(__probeForbiddenForTests(context, "typeof process")).toBe(
      "undefined",
    );
  });

  it("global is undefined", () => {
    expect(__probeForbiddenForTests(context, "typeof global")).toBe(
      "undefined",
    );
  });

  it("globalThis.process is undefined", () => {
    expect(__probeForbiddenForTests(context, "typeof globalThis.process")).toBe(
      "undefined",
    );
  });

  it("require is undefined", () => {
    expect(__probeForbiddenForTests(context, "typeof require")).toBe(
      "undefined",
    );
  });

  it("fetch is undefined", () => {
    expect(__probeForbiddenForTests(context, "typeof fetch")).toBe("undefined");
  });

  it("XMLHttpRequest is undefined", () => {
    expect(__probeForbiddenForTests(context, "typeof XMLHttpRequest")).toBe(
      "undefined",
    );
  });

  it("WebSocket is undefined", () => {
    expect(__probeForbiddenForTests(context, "typeof WebSocket")).toBe(
      "undefined",
    );
  });

  it("setTimeout / setInterval are undefined", () => {
    expect(__probeForbiddenForTests(context, "typeof setTimeout")).toBe(
      "undefined",
    );
    expect(__probeForbiddenForTests(context, "typeof setInterval")).toBe(
      "undefined",
    );
  });

  it("Function constructor cannot be reached via codeGeneration", () => {
    // codeGeneration.strings: false forbids new Function(string) and eval(string)
    // even if the sandbox accidentally exposed them.
    expect(() =>
      __probeForbiddenForTests(
        context,
        "new (function(){}).constructor('return 1')",
      ),
    ).toThrow();
  });
});

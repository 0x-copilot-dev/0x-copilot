import { parse } from "@typescript-eslint/parser";

import { ADAPTER_ALLOWLIST } from "@enterprise-search/api-types";

// PRD §9.5 Q2 + D28/D29. The single source of truth for what a tier-2
// adapter is allowed to import lives in
// `packages/service-contracts/src/enterprise_service_contracts/adapter_allowlist.json`
// (loaded here via `@enterprise-search/api-types`). The same JSON feeds the
// AI backend's 6B `AdapterAllowlistAuditor`, so drift between codegen and
// the desktop's load-time scanner is structurally prevented. Adding a name
// to the JSON is a security decision; do not add anything that mutates the
// DOM, opens a network connection, persists state, or reaches back to the
// host.
//
// `react`: explicitly NO `useEffect` / `useLayoutEffect` (side effects —
// D28 forbids); explicitly NO `useRef` (mutable cross-render storage,
// which is the common escape hatch for the D28 contract).
// `@enterprise-search/design-system`: explicitly NO `ThemeProvider`,
// `useTheme`, `Menu`, `Popover*` — each touches `document` / `window` /
// `localStorage` or owns layout context that an adapter must not own.
export const ALLOWED_IMPORTS: Readonly<Record<string, readonly string[]>> =
  ADAPTER_ALLOWLIST.allowed_imports;

export type AllowedImportSource = string;

const FORBIDDEN_GLOBALS: ReadonlySet<string> = new Set<string>(
  ADAPTER_ALLOWLIST.forbidden_globals,
);

export interface AstViolation {
  readonly line: number;
  readonly kind: string;
  readonly detail: string;
}

export type AstScanResult =
  | { readonly ok: true }
  | { readonly ok: false; readonly violations: readonly AstViolation[] };

interface AstNode {
  readonly type: string;
  readonly loc?: { readonly start: { readonly line: number } };
  readonly [key: string]: unknown;
}

function lineOf(node: AstNode | null | undefined): number {
  return node?.loc?.start.line ?? 0;
}

function isAllowedImportSource(value: string): value is AllowedImportSource {
  return Object.prototype.hasOwnProperty.call(ALLOWED_IMPORTS, value);
}

function checkImportDeclaration(
  node: AstNode,
  violations: AstViolation[],
): void {
  const source = node.source as { value?: unknown } | undefined;
  const rawValue = source?.value;
  if (typeof rawValue !== "string") {
    violations.push({
      line: lineOf(node),
      kind: "import.non-string-source",
      detail: "import source is not a string literal",
    });
    return;
  }
  if (!isAllowedImportSource(rawValue)) {
    violations.push({
      line: lineOf(node),
      kind: "import.disallowed-module",
      detail: `import from "${rawValue}" is not in ALLOWED_IMPORTS`,
    });
    return;
  }
  const allowedNames = new Set<string>(ALLOWED_IMPORTS[rawValue]);
  const specifiers = (node.specifiers ?? []) as AstNode[];
  for (const spec of specifiers) {
    if (spec.type === "ImportDefaultSpecifier") {
      violations.push({
        line: lineOf(spec),
        kind: "import.default-not-allowed",
        detail: `default import from "${rawValue}" is not allowed`,
      });
      continue;
    }
    if (spec.type === "ImportNamespaceSpecifier") {
      violations.push({
        line: lineOf(spec),
        kind: "import.namespace-not-allowed",
        detail: `namespace import from "${rawValue}" is not allowed (* as X)`,
      });
      continue;
    }
    if (spec.type === "ImportSpecifier") {
      const imported = spec.imported as { name?: unknown } | undefined;
      const importedName =
        typeof imported?.name === "string" ? imported.name : null;
      if (importedName === null) {
        violations.push({
          line: lineOf(spec),
          kind: "import.computed-specifier",
          detail: `non-identifier import specifier from "${rawValue}"`,
        });
        continue;
      }
      if (!allowedNames.has(importedName)) {
        violations.push({
          line: lineOf(spec),
          kind: "import.disallowed-specifier",
          detail: `"${importedName}" is not in the allowlist for "${rawValue}"`,
        });
      }
    }
  }
}

function isIdentifierKeyOfMember(
  parent: AstNode | null,
  node: AstNode,
): boolean {
  if (parent === null) return false;
  if (parent.type !== "MemberExpression") return false;
  if ((parent.computed as boolean | undefined) === true) return false;
  return parent.property === node;
}

function isPropertyKey(parent: AstNode | null, node: AstNode): boolean {
  if (parent === null) return false;
  if (parent.type !== "Property" && parent.type !== "PropertyDefinition") {
    return false;
  }
  if ((parent.computed as boolean | undefined) === true) return false;
  return parent.key === node;
}

function isInDeclarationPosition(
  parent: AstNode | null,
  node: AstNode,
): boolean {
  if (parent === null) return false;
  if (parent.type === "VariableDeclarator" && parent.id === node) return true;
  if (
    (parent.type === "FunctionDeclaration" ||
      parent.type === "FunctionExpression" ||
      parent.type === "ArrowFunctionExpression") &&
    parent.id === node
  ) {
    return true;
  }
  if (
    (parent.type === "ImportSpecifier" ||
      parent.type === "ImportDefaultSpecifier" ||
      parent.type === "ImportNamespaceSpecifier") &&
    parent.local === node
  ) {
    return true;
  }
  if (
    (parent.type === "RestElement" || parent.type === "AssignmentPattern") &&
    parent.argument === node
  ) {
    return true;
  }
  if (parent.type === "CatchClause" && parent.param === node) return true;
  return false;
}

function isParamOfFunction(parent: AstNode | null, node: AstNode): boolean {
  if (parent === null) return false;
  if (
    parent.type !== "FunctionDeclaration" &&
    parent.type !== "FunctionExpression" &&
    parent.type !== "ArrowFunctionExpression"
  ) {
    return false;
  }
  const params = (parent.params ?? []) as AstNode[];
  return params.includes(node);
}

function walk(
  node: AstNode | AstNode[] | null | undefined,
  parent: AstNode | null,
  visit: (n: AstNode, p: AstNode | null) => void,
): void {
  if (node === null || node === undefined) return;
  if (Array.isArray(node)) {
    for (const child of node) walk(child, parent, visit);
    return;
  }
  if (typeof node !== "object") return;
  if (typeof node.type !== "string") return;
  visit(node, parent);
  for (const key of Object.keys(node)) {
    if (
      key === "type" ||
      key === "loc" ||
      key === "range" ||
      key === "parent"
    ) {
      continue;
    }
    const value = (node as Record<string, unknown>)[key];
    if (value === null || value === undefined) continue;
    if (Array.isArray(value)) {
      for (const child of value) {
        if (child && typeof child === "object" && "type" in child) {
          walk(child as AstNode, node, visit);
        }
      }
      continue;
    }
    if (
      typeof value === "object" &&
      "type" in (value as Record<string, unknown>)
    ) {
      walk(value as AstNode, node, visit);
    }
  }
}

export function astAllowlistScan(source: string): AstScanResult {
  let ast: AstNode;
  try {
    ast = parse(source, {
      ecmaVersion: 2022,
      sourceType: "module",
      jsx: true,
      loc: true,
      range: false,
    }) as unknown as AstNode;
  } catch (err) {
    const detail = err instanceof Error ? err.message : String(err);
    return {
      ok: false,
      violations: [{ line: 0, kind: "parse-error", detail }],
    };
  }

  const violations: AstViolation[] = [];

  walk(ast, null, (node, parent) => {
    switch (node.type) {
      case "ImportDeclaration":
        checkImportDeclaration(node, violations);
        return;
      case "ImportExpression":
        violations.push({
          line: lineOf(node),
          kind: "dynamic-import",
          detail: "dynamic import() is not allowed",
        });
        return;
      case "CallExpression": {
        const callee = node.callee as AstNode | undefined;
        if (!callee) return;
        if (callee.type === "Identifier") {
          const name = callee.name as string;
          if (name === "require") {
            violations.push({
              line: lineOf(node),
              kind: "require-call",
              detail: "require() is not allowed",
            });
          } else if (name === "eval") {
            violations.push({
              line: lineOf(node),
              kind: "eval-call",
              detail: "eval() is not allowed",
            });
          } else if (name === "Function") {
            violations.push({
              line: lineOf(node),
              kind: "function-constructor-call",
              detail: "Function() invocation is not allowed",
            });
          }
        }
        return;
      }
      case "NewExpression": {
        const callee = node.callee as AstNode | undefined;
        if (callee && callee.type === "Identifier") {
          const name = callee.name as string;
          if (name === "Function") {
            violations.push({
              line: lineOf(node),
              kind: "function-constructor-new",
              detail: "new Function() is not allowed",
            });
          }
        }
        return;
      }
      case "MemberExpression": {
        const prop = node.property as AstNode | undefined;
        const computed = (node.computed as boolean | undefined) === true;
        if (
          !computed &&
          prop &&
          prop.type === "Identifier" &&
          (prop.name as string) === "__proto__"
        ) {
          violations.push({
            line: lineOf(node),
            kind: "proto-access",
            detail: "__proto__ access is not allowed",
          });
        }
        return;
      }
      case "AssignmentExpression": {
        const left = node.left as AstNode | undefined;
        if (left && left.type === "MemberExpression") {
          const prop = left.property as AstNode | undefined;
          if (
            (left.computed as boolean | undefined) !== true &&
            prop &&
            prop.type === "Identifier" &&
            (prop.name as string) === "prototype"
          ) {
            violations.push({
              line: lineOf(node),
              kind: "prototype-mutation",
              detail: "writing to .prototype is not allowed",
            });
          }
        }
        return;
      }
      case "Identifier": {
        const name = node.name as string;
        if (!FORBIDDEN_GLOBALS.has(name)) return;
        if (isIdentifierKeyOfMember(parent, node)) return;
        if (isPropertyKey(parent, node)) return;
        if (isInDeclarationPosition(parent, node)) return;
        if (isParamOfFunction(parent, node)) return;
        if (parent && parent.type === "LabeledStatement") return;
        violations.push({
          line: lineOf(node),
          kind: "forbidden-global",
          detail: `reference to forbidden global "${name}"`,
        });
        return;
      }
      default:
        return;
    }
  });

  if (violations.length === 0) {
    return { ok: true };
  }
  return { ok: false, violations };
}

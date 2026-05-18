// `CodeWizard` — 4-step machine for tools-prd §2 U3 (code-routines).
//
// Steps:
//   1. Identity — name + description + scope picker.
//   2. Code — monospace textarea with line numbers (CSS counter-reset).
//      The AST allow-list is rendered as a read-only "What's allowed"
//      expander. NO Monaco / CodeMirror in chat-surface (P12 polish may
//      wire minimal Monaco in apps/frontend; see orchestrator notes).
//   3. Args schema — JSON textarea with inline validity hint.
//   4. Test — sample args + result display.
//
// Substitution: `onTestCall` is the host callback. `onSave` receives a
// `CreateToolRequest` mapped onto the §3.1 wire shape.

import {
  useCallback,
  useMemo,
  useState,
  type CSSProperties,
  type ReactElement,
} from "react";

import { Button, TextInput } from "@enterprise-search/design-system";
import type {
  CreateToolRequest,
  TestToolCallRequest,
  TestToolCallResponse,
  ToolScope,
} from "@enterprise-search/api-types";

import { useStepMachine } from "./useStepMachine";
import { WizardShell, type WizardStepDescriptor } from "./WizardShell";

const STEPS: ReadonlyArray<WizardStepDescriptor> = [
  { id: "identity", label: "Identity" },
  { id: "code", label: "Code" },
  { id: "args", label: "Args schema" },
  { id: "test", label: "Test" },
];

/** Python AST allow-list rendered in the "What's allowed" expander.
 *  Mirrors §9.1 sandbox spec — frozen + read-only on this surface. */
const ALLOW_LIST: ReadonlyArray<string> = [
  "Module, FunctionDef, AsyncFunctionDef, arguments, arg, Return",
  "Assign, AugAssign, AnnAssign, Name, Constant, Attribute, Subscript",
  "If, For, While, Break, Continue, Pass, Try, ExceptHandler, Raise",
  "Compare, BoolOp, UnaryOp, BinOp, IfExp, Lambda, Tuple, List, Dict, Set",
  "Call (resolved against the import allow-list)",
  "BANNED: Import * (only explicit imports), exec, eval, compile, __import__",
  "BANNED: open with mode 'w','a','x' outside /tmp",
  "BANNED: subprocess, os.system, os.popen",
];

export interface CodeWizardValue {
  readonly name: string;
  readonly description: string;
  readonly scope: ToolScope;
  readonly source: string;
  readonly args_schema_text: string;
  readonly sample_args_text: string;
}

const DEFAULT_VALUE: CodeWizardValue = {
  name: "",
  description: "",
  scope: "read",
  source: 'def run(args):\n    # your code here\n    return {"ok": True}\n',
  args_schema_text: '{\n  "type": "object",\n  "properties": {}\n}\n',
  sample_args_text: "{}",
};

export interface CodeWizardProps {
  readonly initialValue?: Partial<CodeWizardValue>;
  /**
   * Host-supplied test caller. The wizard parses `sample_args_text` to
   * JSON and passes it as `args`; if parse fails the test button stays
   * disabled.
   */
  readonly onTestCall: (
    req: TestToolCallRequest,
  ) => Promise<TestToolCallResponse>;
  readonly onSave: (req: CreateToolRequest) => void;
  readonly onCancel?: () => void;
}

export function CodeWizard(props: CodeWizardProps): ReactElement {
  const { initialValue, onTestCall, onSave, onCancel } = props;
  const stepper = useStepMachine({ totalSteps: STEPS.length });

  const [value, setValue] = useState<CodeWizardValue>(() => ({
    ...DEFAULT_VALUE,
    ...(initialValue ?? {}),
  }));

  const [testState, setTestState] = useState<
    | { kind: "idle" }
    | { kind: "running" }
    | { kind: "result"; response: TestToolCallResponse }
  >({ kind: "idle" });

  const updateField = useCallback(
    <K extends keyof CodeWizardValue>(key: K, next: CodeWizardValue[K]) => {
      setValue((prev) => ({ ...prev, [key]: next }));
    },
    [],
  );

  // -- JSON validity of args_schema / sample_args -------------------------
  const argsSchemaParse = useMemo(
    () => safeJsonParse(value.args_schema_text),
    [value.args_schema_text],
  );
  const sampleArgsParse = useMemo(
    () => safeJsonParse(value.sample_args_text),
    [value.sample_args_text],
  );

  // -- Step gating --------------------------------------------------------
  const canAdvance = useMemo(() => {
    switch (stepper.currentStep) {
      case 0:
        return value.name.trim().length > 0;
      case 1:
        return value.source.trim().length > 0;
      case 2:
        return argsSchemaParse.kind === "ok";
      case 3:
        return true;
      default:
        return false;
    }
  }, [stepper.currentStep, value, argsSchemaParse.kind]);

  // -- Test handler -------------------------------------------------------
  const handleTest = useCallback(async () => {
    if (sampleArgsParse.kind !== "ok") return;
    setTestState({ kind: "running" });
    try {
      const response = await onTestCall({
        args: sampleArgsParse.value as Record<string, unknown>,
      });
      setTestState({ kind: "result", response });
    } catch (e: unknown) {
      const message = e instanceof Error ? e.message : "Test call failed.";
      setTestState({
        kind: "result",
        response: {
          status: "error",
          latency_ms: 0,
          error: { kind: "unknown", message },
        },
      });
    }
  }, [onTestCall, sampleArgsParse]);

  const handleSave = useCallback(() => {
    if (argsSchemaParse.kind !== "ok") return;
    const req: CreateToolRequest = {
      kind: "code",
      name: value.name,
      description: value.description,
      scope: value.scope,
      args_schema: argsSchemaParse.value as Record<string, unknown>,
      returns_schema: {},
      transport: {
        kind: "sandbox",
        executor: value.name,
      },
    };
    onSave(req);
  }, [argsSchemaParse, value, onSave]);

  return (
    <WizardShell
      steps={STEPS}
      currentStep={stepper.currentStep}
      title="Author a code-routine"
      subtitle="Write deterministic Python that runs in the sandbox. Static-analysis is enforced server-side."
      onBack={stepper.back}
      onNext={stepper.next}
      onFinish={handleSave}
      finishLabel="Save"
      nextDisabled={!canAdvance}
      finishDisabled={testState.kind !== "result"}
      footerSlot={
        onCancel !== undefined && stepper.currentStep === 0 ? (
          <Button
            variant="ghost"
            size="md"
            onClick={onCancel}
            data-testid="code-wizard-cancel"
          >
            Cancel
          </Button>
        ) : null
      }
      testIdPrefix="code"
    >
      {stepper.currentStep === 0 ? (
        <IdentityStep
          value={value}
          onChangeName={(v) => updateField("name", v)}
          onChangeDescription={(v) => updateField("description", v)}
          onChangeScope={(v) => updateField("scope", v)}
        />
      ) : null}
      {stepper.currentStep === 1 ? (
        <CodeStep
          source={value.source}
          onChangeSource={(v) => updateField("source", v)}
        />
      ) : null}
      {stepper.currentStep === 2 ? (
        <ArgsSchemaStep
          schemaText={value.args_schema_text}
          schemaParse={argsSchemaParse}
          onChange={(v) => updateField("args_schema_text", v)}
        />
      ) : null}
      {stepper.currentStep === 3 ? (
        <TestStep
          sampleArgsText={value.sample_args_text}
          sampleParse={sampleArgsParse}
          onChangeSampleArgs={(v) => updateField("sample_args_text", v)}
          testState={testState}
          onTest={handleTest}
        />
      ) : null}
    </WizardShell>
  );
}

// ---------------------------------------------------------------------------
// Step 1 — identity.
// ---------------------------------------------------------------------------

interface IdentityStepProps {
  readonly value: CodeWizardValue;
  readonly onChangeName: (v: string) => void;
  readonly onChangeDescription: (v: string) => void;
  readonly onChangeScope: (v: ToolScope) => void;
}

function IdentityStep(props: IdentityStepProps): ReactElement {
  const { value, onChangeName, onChangeDescription, onChangeScope } = props;
  return (
    <div data-testid="code-wizard-identity">
      <label style={labelStyle}>
        Name
        <TextInput
          aria-label="Name"
          value={value.name}
          onChange={(e) => onChangeName(e.target.value)}
          placeholder="e.g. crm-cleanup"
          data-testid="code-wizard-name"
        />
      </label>
      <label style={labelStyle}>
        Description
        <TextInput
          aria-label="Description"
          value={value.description}
          onChange={(e) => onChangeDescription(e.target.value)}
          placeholder="One sentence — what this routine does."
          data-testid="code-wizard-description"
        />
      </label>
      <div
        role="radiogroup"
        aria-label="Scope"
        style={radioGroupStyle}
        data-testid="code-wizard-scope"
      >
        <span style={labelTextStyle}>Scope</span>
        {(["read", "write", "both"] as ReadonlyArray<ToolScope>).map((s) => (
          <label key={s} style={labelRowStyle}>
            <input
              type="radio"
              name="code-wizard-scope"
              value={s}
              checked={value.scope === s}
              onChange={() => onChangeScope(s)}
              data-testid={`code-wizard-scope-${s}`}
            />
            <span>{s}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 2 — code editor (textarea + CSS-counter line numbers).
// ---------------------------------------------------------------------------

interface CodeStepProps {
  readonly source: string;
  readonly onChangeSource: (v: string) => void;
}

function CodeStep(props: CodeStepProps): ReactElement {
  const { source, onChangeSource } = props;
  const [allowedOpen, setAllowedOpen] = useState(false);
  const lineCount = useMemo(
    () => Math.max(1, source.split("\n").length),
    [source],
  );

  return (
    <div data-testid="code-wizard-code">
      <p style={hintStyle}>
        Python 3.13. <code>def run(args)</code> is the entry point. The sandbox
        enforces a static-analysis allow-list (see &quot;What&apos;s
        allowed&quot;).
      </p>
      <div style={editorBoxStyle}>
        <div aria-hidden="true" style={gutterStyle}>
          {Array.from({ length: lineCount }, (_, i) => (
            <span key={i} style={gutterLineStyle}>
              {i + 1}
            </span>
          ))}
        </div>
        <textarea
          aria-label="Routine source"
          value={source}
          onChange={(e) => onChangeSource(e.target.value)}
          data-testid="code-wizard-source"
          rows={Math.max(10, Math.min(24, lineCount + 2))}
          spellCheck={false}
          style={editorTextareaStyle}
        />
      </div>
      <details
        style={detailsStyle}
        onToggle={(e) => setAllowedOpen((e.target as HTMLDetailsElement).open)}
        open={allowedOpen}
        data-testid="code-wizard-allowed-details"
      >
        <summary style={summaryStyle}>What&apos;s allowed</summary>
        <ul style={allowListStyle}>
          {ALLOW_LIST.map((line) => (
            <li key={line}>
              <code style={codeStyle}>{line}</code>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 3 — args schema editor.
// ---------------------------------------------------------------------------

interface ArgsSchemaStepProps {
  readonly schemaText: string;
  readonly schemaParse: JsonParseResult;
  readonly onChange: (v: string) => void;
}

function ArgsSchemaStep(props: ArgsSchemaStepProps): ReactElement {
  const { schemaText, schemaParse, onChange } = props;
  return (
    <div data-testid="code-wizard-args">
      <p style={hintStyle}>
        Args JSON Schema (Draft 2020-12). Server validates this before dispatch.
      </p>
      <textarea
        aria-label="Args JSON Schema"
        value={schemaText}
        onChange={(e) => onChange(e.target.value)}
        data-testid="code-wizard-args-schema"
        rows={14}
        spellCheck={false}
        style={jsonTextareaStyle}
      />
      {schemaParse.kind === "error" ? (
        <p role="alert" style={errorStyle} data-testid="code-wizard-args-error">
          Invalid JSON: {schemaParse.message}
        </p>
      ) : (
        <p role="status" style={successStyle} data-testid="code-wizard-args-ok">
          Valid JSON.
        </p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 4 — test call.
// ---------------------------------------------------------------------------

interface TestStepProps {
  readonly sampleArgsText: string;
  readonly sampleParse: JsonParseResult;
  readonly onChangeSampleArgs: (v: string) => void;
  readonly testState:
    | { kind: "idle" }
    | { kind: "running" }
    | { kind: "result"; response: TestToolCallResponse };
  readonly onTest: () => void;
}

function TestStep(props: TestStepProps): ReactElement {
  const { sampleArgsText, sampleParse, onChangeSampleArgs, testState, onTest } =
    props;
  return (
    <div data-testid="code-wizard-test">
      <label style={labelStyle}>
        Sample args (JSON)
        <textarea
          aria-label="Sample args"
          value={sampleArgsText}
          onChange={(e) => onChangeSampleArgs(e.target.value)}
          data-testid="code-wizard-sample-args"
          rows={6}
          spellCheck={false}
          style={jsonTextareaStyle}
        />
      </label>
      {sampleParse.kind === "error" ? (
        <p
          role="alert"
          style={errorStyle}
          data-testid="code-wizard-sample-error"
        >
          Invalid JSON: {sampleParse.message}
        </p>
      ) : null}
      <Button
        variant="primary"
        size="md"
        onClick={onTest}
        disabled={sampleParse.kind !== "ok" || testState.kind === "running"}
        data-testid="code-wizard-test-run"
      >
        {testState.kind === "running" ? "Running…" : "Run test"}
      </Button>
      {testState.kind === "result" ? (
        <div
          role="status"
          style={testResultStyle(testState.response.status)}
          data-testid="code-wizard-test-result"
          data-status={testState.response.status}
        >
          <strong>{testState.response.status === "ok" ? "OK" : "Error"}</strong>{" "}
          ({testState.response.latency_ms} ms)
          {testState.response.error !== undefined ? (
            <p style={mutedStyle}>
              {testState.response.error.kind}:{" "}
              {testState.response.error.message}
            </p>
          ) : null}
          {testState.response.result !== undefined ? (
            <pre style={preStyle}>
              {JSON.stringify(testState.response.result, null, 2)}
            </pre>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// ---------------------------------------------------------------------------
// JSON parse helper.
// ---------------------------------------------------------------------------

type JsonParseResult =
  | { readonly kind: "ok"; readonly value: unknown }
  | { readonly kind: "error"; readonly message: string };

function safeJsonParse(text: string): JsonParseResult {
  if (text.trim().length === 0) {
    return { kind: "error", message: "empty input" };
  }
  try {
    return { kind: "ok", value: JSON.parse(text) as unknown };
  } catch (e: unknown) {
    const message = e instanceof Error ? e.message : "parse failed";
    return { kind: "error", message };
  }
}

// ---------------------------------------------------------------------------
// Styles.
// ---------------------------------------------------------------------------

const hintStyle: CSSProperties = {
  margin: 0,
  fontSize: 12.5,
  color: "var(--color-text-muted)",
};

const labelStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 4,
  fontSize: 12,
  fontWeight: 600,
  color: "var(--color-text-muted)",
  marginBottom: 8,
};

const labelTextStyle: CSSProperties = {
  fontSize: 12,
  fontWeight: 600,
  color: "var(--color-text-muted)",
};

const radioGroupStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  marginTop: 8,
};

const labelRowStyle: CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
  fontSize: 13,
  color: "var(--color-text)",
};

const editorBoxStyle: CSSProperties = {
  display: "flex",
  background: "var(--color-bg-elevated)",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  overflow: "hidden",
};

const gutterStyle: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  padding: "8px 6px",
  borderRight: "1px solid var(--color-border)",
  background: "var(--color-bg)",
  fontFamily:
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
  fontSize: 12,
  color: "var(--color-text-muted)",
  userSelect: "none",
  textAlign: "right",
  minWidth: 32,
};

const gutterLineStyle: CSSProperties = {
  lineHeight: "18px",
};

const editorTextareaStyle: CSSProperties = {
  flex: 1,
  padding: "8px 10px",
  border: "none",
  outline: "none",
  background: "transparent",
  color: "var(--color-text)",
  fontFamily:
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
  fontSize: 12.5,
  lineHeight: "18px",
  resize: "vertical",
};

const jsonTextareaStyle: CSSProperties = {
  padding: "8px 10px",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  outline: "none",
  background: "var(--color-bg-elevated)",
  color: "var(--color-text)",
  fontFamily:
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
  fontSize: 12.5,
  lineHeight: "18px",
  resize: "vertical",
  width: "100%",
  boxSizing: "border-box",
};

const detailsStyle: CSSProperties = {
  marginTop: 8,
  padding: "6px 10px",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  background: "var(--color-bg-elevated)",
};

const summaryStyle: CSSProperties = {
  cursor: "pointer",
  fontSize: 13,
  fontWeight: 600,
  color: "var(--color-text)",
};

const allowListStyle: CSSProperties = {
  margin: "8px 0 0 0",
  paddingLeft: 18,
  fontSize: 12,
  color: "var(--color-text-muted)",
  display: "flex",
  flexDirection: "column",
  gap: 2,
};

const codeStyle: CSSProperties = {
  fontFamily:
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
  fontSize: 12,
  color: "var(--color-text)",
};

const errorStyle: CSSProperties = {
  margin: "6px 0 0 0",
  fontSize: 13,
  color: "var(--color-danger, #dc2626)",
};

const successStyle: CSSProperties = {
  margin: "6px 0 0 0",
  fontSize: 13,
  color: "var(--color-success, #16a34a)",
};

const mutedStyle: CSSProperties = {
  fontSize: 12.5,
  color: "var(--color-text-muted)",
};

const testResultStyle = (status: "ok" | "error"): CSSProperties => ({
  marginTop: 12,
  padding: 10,
  borderRadius: 6,
  border: `1px solid ${
    status === "ok"
      ? "var(--color-success, #16a34a)"
      : "var(--color-danger, #dc2626)"
  }`,
  background: "var(--color-bg-elevated)",
  color: "var(--color-text)",
  fontSize: 13,
});

const preStyle: CSSProperties = {
  margin: "8px 0 0 0",
  padding: 8,
  borderRadius: 4,
  background: "var(--color-bg)",
  fontSize: 12,
  fontFamily:
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace",
  maxHeight: 200,
  overflow: "auto",
  whiteSpace: "pre-wrap",
};

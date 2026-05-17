import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import { MentionPopover, type MentionCandidate } from "./MentionPopover";
import { ModelPicker } from "./ModelPicker";
import { ToolPicker } from "./ToolPicker";

export interface ComposerProps {
  readonly onSend: (text: string) => void;
  readonly disabled?: boolean;
  readonly placeholder?: string;
  readonly initialModel?: string;
  readonly initialTools?: ReadonlyArray<string>;
  readonly portalTarget?: HTMLElement;
}

interface MentionTriggerState {
  readonly start: number;
  readonly query: string;
}

const DEFAULT_MODEL = "claude-opus-4-7";

export function Composer(props: ComposerProps): ReactNode {
  const {
    onSend,
    disabled = false,
    placeholder = "Send a message…",
    initialModel = DEFAULT_MODEL,
    initialTools,
    portalTarget,
  } = props;

  const [text, setText] = useState("");
  const [model, setModel] = useState(initialModel);
  const [tools, setTools] = useState<ReadonlyArray<string>>(initialTools ?? []);
  const [toolPickerOpen, setToolPickerOpen] = useState(false);
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [mention, setMention] = useState<MentionTriggerState | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const el = textareaRef.current;
    if (el === null) {
      return;
    }
    el.style.height = "auto";
    const next = Math.min(el.scrollHeight, MAX_TEXTAREA_HEIGHT_PX);
    el.style.height = `${next}px`;
  }, [text]);

  const detectMention = useCallback(
    (value: string, caret: number): MentionTriggerState | null => {
      const upto = value.slice(0, caret);
      const at = upto.lastIndexOf("@");
      if (at === -1) {
        return null;
      }
      const before = at === 0 ? " " : upto[at - 1];
      if (before !== " " && before !== "\n") {
        return null;
      }
      const query = upto.slice(at + 1);
      if (/\s/.test(query)) {
        return null;
      }
      return { start: at, query };
    },
    [],
  );

  const handleTextChange = (next: string, caret: number): void => {
    setText(next);
    setMention(detectMention(next, caret));
  };

  const send = (): void => {
    const trimmed = text.trim();
    if (trimmed.length === 0 || disabled) {
      return;
    }
    onSend(trimmed);
    setText("");
    setMention(null);
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
      return;
    }
    if (event.key === "Escape") {
      if (toolPickerOpen) {
        setToolPickerOpen(false);
      }
      if (modelPickerOpen) {
        setModelPickerOpen(false);
      }
      if (mention !== null) {
        setMention(null);
      }
    }
  };

  const insertMention = (candidate: MentionCandidate): void => {
    if (mention === null) {
      return;
    }
    const before = text.slice(0, mention.start);
    const after = text.slice(mention.start + 1 + mention.query.length);
    const inserted = `@${candidate.slug} `;
    const next = `${before}${inserted}${after}`;
    setText(next);
    setMention(null);
    const el = textareaRef.current;
    if (el !== null) {
      const pos = before.length + inserted.length;
      Promise.resolve().then(() => {
        el.focus();
        el.setSelectionRange(pos, pos);
      });
    }
  };

  const toggleTool = (name: string): void => {
    setTools((prev) =>
      prev.includes(name) ? prev.filter((t) => t !== name) : [...prev, name],
    );
  };

  const canSend = text.trim().length > 0 && !disabled;

  return (
    <div data-testid="composer" style={containerStyle} aria-disabled={disabled}>
      <textarea
        ref={textareaRef}
        value={text}
        disabled={disabled}
        placeholder={placeholder}
        onChange={(e) =>
          handleTextChange(e.target.value, e.target.selectionStart ?? 0)
        }
        onKeyDown={handleKeyDown}
        onKeyUp={(e) => {
          const target = e.currentTarget;
          handleTextChange(target.value, target.selectionStart ?? 0);
        }}
        rows={1}
        aria-label="Message"
        style={textareaStyle}
        data-testid="composer-textarea"
      />
      <div style={toolbarStyle}>
        <div style={toolbarLeftStyle}>
          <button
            type="button"
            onClick={() => setToolPickerOpen((v) => !v)}
            aria-pressed={toolPickerOpen}
            aria-label="Tools"
            data-testid="composer-tools-toggle"
            style={iconButtonStyle(toolPickerOpen)}
          >
            {tools.length > 0 ? `Tools · ${tools.length}` : "Tools"}
          </button>
          <button
            type="button"
            onClick={() => setModelPickerOpen((v) => !v)}
            aria-pressed={modelPickerOpen}
            aria-label="Model"
            data-testid="composer-model-toggle"
            style={iconButtonStyle(modelPickerOpen)}
          >
            {labelForModel(model)}
          </button>
        </div>
        <button
          type="button"
          onClick={send}
          disabled={!canSend}
          data-testid="composer-send"
          style={sendButtonStyle(canSend)}
        >
          Send
        </button>
      </div>
      {toolPickerOpen ? (
        <div style={popoverHostStyle}>
          <ToolPicker
            open={true}
            selectedTools={tools}
            onToggle={toggleTool}
            onClose={() => setToolPickerOpen(false)}
            portalTarget={portalTarget}
          />
        </div>
      ) : null}
      {modelPickerOpen ? (
        <div style={popoverHostStyle}>
          <ModelPicker
            open={true}
            selectedModel={model}
            onSelect={setModel}
            onClose={() => setModelPickerOpen(false)}
            portalTarget={portalTarget}
          />
        </div>
      ) : null}
      {mention !== null ? (
        <div style={popoverHostStyle}>
          <MentionPopover
            open={true}
            query={mention.query}
            onSelect={insertMention}
            onClose={() => setMention(null)}
            portalTarget={portalTarget}
          />
        </div>
      ) : null}
    </div>
  );
}

function labelForModel(id: string): string {
  if (id === "claude-opus-4-7") {
    return "Opus 4.7";
  }
  if (id === "claude-sonnet-4-6") {
    return "Sonnet 4.6";
  }
  if (id === "claude-haiku-4-5") {
    return "Haiku 4.5";
  }
  return id;
}

const MAX_TEXTAREA_HEIGHT_PX = 200;

const PALETTE = {
  cardBg: "#181a1c",
  cardBorder: "#2a2d31",
  cardBorderActive: "#3a3e44",
  inputBg: "#0f1112",
  textHi: "#f4f5f6",
  textLo: "#9aa0a6",
  accent: "#c2ff5a",
  accentMuted: "rgba(194, 255, 90, 0.35)",
} as const;

const containerStyle: CSSProperties = {
  background: PALETTE.cardBg,
  border: `1px solid ${PALETTE.cardBorder}`,
  borderRadius: 12,
  padding: 10,
  display: "flex",
  flexDirection: "column",
  gap: 8,
  fontFamily:
    "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
  color: PALETTE.textHi,
  position: "relative",
};

const textareaStyle: CSSProperties = {
  background: PALETTE.inputBg,
  color: PALETTE.textHi,
  border: `1px solid ${PALETTE.cardBorder}`,
  borderRadius: 8,
  padding: "10px 12px",
  fontSize: 14,
  lineHeight: 1.45,
  resize: "none",
  outline: "none",
  width: "100%",
  fontFamily: "inherit",
  minHeight: 40,
  maxHeight: MAX_TEXTAREA_HEIGHT_PX,
  overflowY: "auto",
};

const toolbarStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
};

const toolbarLeftStyle: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
};

const iconButtonStyle = (active: boolean): CSSProperties => ({
  background: active ? PALETTE.cardBorderActive : "transparent",
  color: PALETTE.textLo,
  border: `1px solid ${PALETTE.cardBorder}`,
  borderRadius: 6,
  padding: "4px 10px",
  fontSize: 12,
  cursor: "pointer",
});

const sendButtonStyle = (enabled: boolean): CSSProperties => ({
  background: enabled ? PALETTE.accent : PALETTE.accentMuted,
  color: PALETTE.cardBg,
  border: "none",
  borderRadius: 8,
  padding: "6px 14px",
  fontSize: 13,
  fontWeight: 600,
  cursor: enabled ? "pointer" : "not-allowed",
});

const popoverHostStyle: CSSProperties = {
  marginTop: 4,
};

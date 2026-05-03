import type { ToolCallMessagePartProps } from "@assistant-ui/react";
import { Button } from "@enterprise-search/design-system";
import { useState, type ReactElement } from "react";
import { stringValue } from "../../utils/jsonUtils";
import { ActivityCard } from "../activity/ActivityCard";
import { presentationFromArgs } from "../activity/presentationHelpers";
import { approvalDetailsContent } from "../details/approvalDetailsContent";

export function AskAQuestionTool({
  args,
  approvalId,
  resolved,
  result,
  presentation,
  resume,
}: {
  args: Record<string, unknown>;
  approvalId: string;
  resolved: boolean;
  result: unknown;
  presentation: ReturnType<typeof presentationFromArgs>;
  resume: ToolCallMessagePartProps<Record<string, unknown>>["resume"];
}): ReactElement {
  const question =
    stringValue(args.question) ?? stringValue(args.message) ?? "";
  const hint = stringValue(args.hint);
  const options = Array.isArray(args.options)
    ? (args.options.filter((option) => typeof option === "string") as string[])
    : [];
  const [draft, setDraft] = useState("");
  const submit = (answer: string): void => {
    const trimmed = answer.trim();
    if (!trimmed) {
      return;
    }
    resume({
      decision: "approved",
      approval_id: approvalId,
      approval_kind: "ask_a_question",
      answer: trimmed,
    });
  };
  const decline = (): void => {
    resume({
      decision: "rejected",
      approval_id: approvalId,
      approval_kind: "ask_a_question",
    });
  };
  const submittedAnswer =
    result && typeof result === "object" && "answer" in result
      ? stringValue((result as Record<string, unknown>).answer)
      : null;
  return (
    <ActivityCard
      title={
        presentation?.title ??
        (resolved ? "Question answered" : "Question for you")
      }
      status={
        presentation?.status_label ?? (resolved ? "Answered" : "Awaiting reply")
      }
      variant="approval"
      description={presentation?.summary ?? question}
      params={hint ? [{ label: "Hint", value: hint }] : []}
      details={approvalDetailsContent(args, result)}
      detailsLabel={presentation?.debug_label ?? "Question details"}
    >
      {resolved ? (
        submittedAnswer ? (
          <div className="aui-tool-card__answer">{submittedAnswer}</div>
        ) : null
      ) : options.length > 0 ? (
        <div className="aui-tool-card__actions">
          {options.map((option) => (
            <Button
              key={option}
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => submit(option)}
            >
              {option}
            </Button>
          ))}
          <Button
            type="button"
            size="sm"
            variant="secondary"
            title="Decline to answer"
            onClick={decline}
          >
            Skip
          </Button>
        </div>
      ) : (
        <form
          className="aui-tool-card__actions"
          onSubmit={(event) => {
            event.preventDefault();
            submit(draft);
          }}
        >
          <input
            type="text"
            className="aui-tool-card__input"
            placeholder="Type your answer"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            autoFocus
          />
          <Button type="submit" size="sm" disabled={draft.trim().length === 0}>
            Send
          </Button>
          <Button type="button" size="sm" variant="secondary" onClick={decline}>
            Skip
          </Button>
        </form>
      )}
    </ActivityCard>
  );
}

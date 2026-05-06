import type { TextMessagePartProps } from "../../runtime/types";
import { classNames } from "@enterprise-search/design-system";
import type { ReactElement } from "react";
import { Streamdown } from "streamdown";
import { MarkdownLink } from "./MarkdownLink";
import { remarkCitations } from "./citationRemarkPlugin";

const markdownComponents = {
  a: MarkdownLink,
};

const remarkPlugins = [remarkCitations];

export function MarkdownText({
  text,
  status,
}: TextMessagePartProps): ReactElement {
  const streaming = status.type === "running";
  return (
    <Streamdown
      animated={
        streaming
          ? {
              animation: "fadeIn",
              duration: 120,
              easing: "ease-out",
              sep: "word",
            }
          : false
      }
      className={classNames(
        "assistant-markdown",
        streaming ? "assistant-markdown--streaming" : undefined,
      )}
      components={markdownComponents}
      isAnimating={streaming}
      mode={streaming ? "streaming" : "static"}
      remarkPlugins={remarkPlugins}
    >
      {text}
    </Streamdown>
  );
}

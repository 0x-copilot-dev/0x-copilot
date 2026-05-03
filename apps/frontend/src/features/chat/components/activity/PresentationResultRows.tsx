import { Badge } from "@enterprise-search/design-system";
import type { RuntimeEventPresentation } from "@enterprise-search/api-types";
import type { ReactElement } from "react";

export function PresentationResultRows({
  rows,
}: {
  rows: RuntimeEventPresentation["result_preview"];
}): ReactElement {
  return (
    <div className="aui-presentation-result">
      {rows?.map((row, index) => (
        <div
          className="aui-presentation-result__row"
          key={`${row.title}-${index}`}
        >
          <div className="aui-presentation-result__text">
            {row.url ? (
              <a href={row.url} target="_blank" rel="noreferrer">
                {row.title}
              </a>
            ) : (
              <span>{row.title}</span>
            )}
            {row.subtitle ? <p>{row.subtitle}</p> : null}
          </div>
          {row.badge ? <Badge>{row.badge}</Badge> : null}
        </div>
      ))}
    </div>
  );
}

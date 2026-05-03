import type { ModelCatalogModel } from "@enterprise-search/api-types";
import type { ReactElement } from "react";

export function ModelSelector({
  models,
  value,
  onChange,
  disabled,
}: {
  models: Array<ModelCatalogModel & { disabled?: boolean }>;
  value: string;
  onChange: (modelId: string) => void;
  disabled?: boolean;
}): ReactElement {
  const selected = models.find((model) => model.id === value) ?? models[0];
  return (
    <label className="aui-model-selector">
      <span className="sr-only">Select model</span>
      <select
        value={selected?.id ?? value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        {models.map((model) => (
          <option key={model.id} value={model.id} disabled={model.disabled}>
            {model.name}
          </option>
        ))}
      </select>
      <span aria-hidden="true">⌄</span>
    </label>
  );
}

"""Layout template builders for the tier-2 render-adapter generator."""

from __future__ import annotations

import json
from typing import ClassVar

from agent_runtime.capabilities.render_adapter_generator.models import (
    LayoutTemplate,
    SampleState,
)


class _PaletteTokens:
    """Inline palette identifiers used by every generated adapter."""

    PAGE_BG: ClassVar[str] = "#101113"
    SURFACE: ClassVar[str] = "#181a1c"
    SURFACE_MUTE: ClassVar[str] = "#1f2226"
    BORDER: ClassVar[str] = "#2a2d31"
    TEXT_HI: ClassVar[str] = "#f4f5f6"
    TEXT_MID: ClassVar[str] = "#c8ccd1"
    TEXT_LO: ClassVar[str] = "#9aa0a6"
    LIME: ClassVar[str] = "#c2ff5a"
    LIME_BG_SOFT: ClassVar[str] = "rgba(194, 255, 90, 0.12)"


class _SchemaVersion:
    """Locked schema version emitted by every Phase-6 template."""

    VALUE: ClassVar[int] = 1


class _GeneratorIdentity:
    """Stable generator identifier embedded in every adapter's metadata."""

    NAME: ClassVar[str] = "render-adapter-generator/v1"


class _Origin:
    """Adapter provenance written into ``metadata.origin``."""

    AGENT_GENERATED: ClassVar[str] = "agent-generated"


class _TsLiteral:
    """Helpers that emit TypeScript literals from validated Python values."""

    @classmethod
    def from_value(cls, value: object) -> str:
        """Return a syntactically valid TS literal for any sample-state value."""
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int | float):
            return json.dumps(value)
        if isinstance(value, str):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, list):
            return "[" + ", ".join(cls.from_value(item) for item in value) + "]"
        if isinstance(value, dict):
            entries = ", ".join(
                f"{json.dumps(str(key))}: {cls.from_value(item)}"
                for key, item in value.items()
            )
            return "{" + entries + "}"
        return "null"

    @classmethod
    def from_string(cls, value: str) -> str:
        return json.dumps(value, ensure_ascii=False)


class _ReactCall:
    """TypeScript ``React.createElement(...)`` call builders."""

    @classmethod
    def element(
        cls,
        tag: str,
        *,
        props: str = "null",
        children: list[str] | None = None,
    ) -> str:
        parts = ["React.createElement(", _TsLiteral.from_string(tag), ", ", props]
        if children:
            for child in children:
                parts.append(", ")
                parts.append(child)
        parts.append(")")
        return "".join(parts)

    @classmethod
    def text(cls, value: str) -> str:
        return _TsLiteral.from_string(value)

    @classmethod
    def expr_text(cls, expression: str) -> str:
        return f"String({expression})"


class _StyleObject:
    """Single source of truth for the inline style objects every template uses."""

    PAGE: ClassVar[str] = (
        "{padding: 24, background: "
        + _TsLiteral.from_string(_PaletteTokens.PAGE_BG)
        + ", color: "
        + _TsLiteral.from_string(_PaletteTokens.TEXT_HI)
        + ', fontFamily: "ui-sans-serif, system-ui, -apple-system"}'
    )
    CARD: ClassVar[str] = (
        "{background: "
        + _TsLiteral.from_string(_PaletteTokens.SURFACE)
        + ", border: "
        + _TsLiteral.from_string("1px solid " + _PaletteTokens.BORDER)
        + ', borderRadius: 14, padding: 22, display: "flex", flexDirection: "column", gap: 14}'
    )
    HEADER: ClassVar[str] = (
        '{display: "flex", justifyContent: "space-between", alignItems: "center",'
        " borderBottom: "
        + _TsLiteral.from_string("1px solid " + _PaletteTokens.BORDER)
        + ", paddingBottom: 10}"
    )
    HEADER_TITLE: ClassVar[str] = (
        '{fontSize: 13, letterSpacing: 0.6, textTransform: "uppercase",'
        " color: " + _TsLiteral.from_string(_PaletteTokens.TEXT_LO) + "}"
    )
    HEADER_ID: ClassVar[str] = (
        "{fontSize: 12, color: " + _TsLiteral.from_string(_PaletteTokens.TEXT_MID) + "}"
    )
    FIELD_ROW: ClassVar[str] = (
        '{display: "grid", gridTemplateColumns: "160px 1fr", gap: 12, paddingBlock: 4}'
    )
    FIELD_LABEL: ClassVar[str] = (
        '{fontSize: 12, letterSpacing: 0.4, textTransform: "uppercase",'
        " color: " + _TsLiteral.from_string(_PaletteTokens.TEXT_LO) + "}"
    )
    FIELD_VALUE: ClassVar[str] = (
        "{fontSize: 13, color: " + _TsLiteral.from_string(_PaletteTokens.TEXT_HI) + "}"
    )
    PROVENANCE_PILL: ClassVar[str] = (
        '{display: "inline-flex", alignItems: "center", gap: 6, padding: "2px 8px",'
        " borderRadius: 999, border: "
        + _TsLiteral.from_string("1px solid " + _PaletteTokens.BORDER)
        + ", fontSize: 11, color: "
        + _TsLiteral.from_string(_PaletteTokens.TEXT_LO)
        + "}"
    )
    PENDING_FIELD: ClassVar[str] = (
        '{display: "grid", gridTemplateColumns: "160px 1fr", gap: 12, paddingBlock: 4,'
        " background: "
        + _TsLiteral.from_string(_PaletteTokens.LIME_BG_SOFT)
        + ", border: "
        + _TsLiteral.from_string("1px solid " + _PaletteTokens.LIME)
        + ", borderRadius: 8, padding: 8}"
    )
    OLD_NEW_PAIR: ClassVar[str] = '{display: "flex", flexDirection: "column", gap: 4}'
    OLD_VALUE: ClassVar[str] = (
        '{fontSize: 12, textDecoration: "line-through",'
        " color: " + _TsLiteral.from_string(_PaletteTokens.TEXT_LO) + "}"
    )
    NEW_VALUE: ClassVar[str] = (
        "{fontSize: 13, color: " + _TsLiteral.from_string(_PaletteTokens.TEXT_HI) + "}"
    )
    TABLE: ClassVar[str] = (
        '{width: "100%", borderCollapse: "collapse", fontSize: 13,'
        " color: " + _TsLiteral.from_string(_PaletteTokens.TEXT_HI) + "}"
    )
    TABLE_HEAD: ClassVar[str] = (
        '{position: "sticky", top: 0, background: '
        + _TsLiteral.from_string(_PaletteTokens.SURFACE_MUTE)
        + ', textAlign: "left", padding: 8, fontSize: 11, textTransform: "uppercase",'
        " color: "
        + _TsLiteral.from_string(_PaletteTokens.TEXT_LO)
        + ", borderBottom: "
        + _TsLiteral.from_string("1px solid " + _PaletteTokens.BORDER)
        + "}"
    )
    TABLE_CELL: ClassVar[str] = (
        "{padding: 8, borderBottom: "
        + _TsLiteral.from_string("1px solid " + _PaletteTokens.BORDER)
        + "}"
    )
    TABLE_CELL_CHANGED: ClassVar[str] = (
        "{padding: 8, background: "
        + _TsLiteral.from_string(_PaletteTokens.LIME_BG_SOFT)
        + ", borderBottom: "
        + _TsLiteral.from_string("1px solid " + _PaletteTokens.LIME)
        + "}"
    )
    KANBAN_BOARD: ClassVar[str] = (
        '{display: "flex", gap: 12, alignItems: "flex-start",'
        " color: " + _TsLiteral.from_string(_PaletteTokens.TEXT_HI) + "}"
    )
    KANBAN_COLUMN: ClassVar[str] = (
        '{flex: 1, minWidth: 220, display: "flex", flexDirection: "column", gap: 10,'
        " background: "
        + _TsLiteral.from_string(_PaletteTokens.SURFACE_MUTE)
        + ", border: "
        + _TsLiteral.from_string("1px solid " + _PaletteTokens.BORDER)
        + ", borderRadius: 10, padding: 12}"
    )
    KANBAN_COL_HEADER: ClassVar[str] = (
        '{fontSize: 11, textTransform: "uppercase", letterSpacing: 0.6,'
        " color: " + _TsLiteral.from_string(_PaletteTokens.TEXT_LO) + "}"
    )
    KANBAN_CARD: ClassVar[str] = (
        "{background: "
        + _TsLiteral.from_string(_PaletteTokens.SURFACE)
        + ", border: "
        + _TsLiteral.from_string("1px solid " + _PaletteTokens.BORDER)
        + ', borderRadius: 8, padding: 10, display: "flex", flexDirection: "column", gap: 6,'
        " fontSize: 13, color: " + _TsLiteral.from_string(_PaletteTokens.TEXT_HI) + "}"
    )
    KANBAN_CARD_CHANGED: ClassVar[str] = (
        "{background: "
        + _TsLiteral.from_string(_PaletteTokens.LIME_BG_SOFT)
        + ", border: "
        + _TsLiteral.from_string("1px solid " + _PaletteTokens.LIME)
        + ', borderRadius: 8, padding: 10, display: "flex", flexDirection: "column", gap: 6,'
        " fontSize: 13, color: " + _TsLiteral.from_string(_PaletteTokens.TEXT_HI) + "}"
    )
    DL: ClassVar[str] = (
        '{display: "grid", gridTemplateColumns: "160px 1fr", gap: 8,'
        " color: " + _TsLiteral.from_string(_PaletteTokens.TEXT_HI) + ", margin: 0}"
    )
    DT: ClassVar[str] = (
        '{fontSize: 12, letterSpacing: 0.4, textTransform: "uppercase",'
        " color: " + _TsLiteral.from_string(_PaletteTokens.TEXT_LO) + ", margin: 0}"
    )
    DD: ClassVar[str] = (
        "{fontSize: 13, margin: 0,"
        " color: " + _TsLiteral.from_string(_PaletteTokens.TEXT_HI) + "}"
    )


class AdapterSourceBuilder:
    """Compose the complete TypeScript source for one generated adapter."""

    IMPORT_LINES: ClassVar[tuple[str, ...]] = (
        'import * as React from "react";',
        'import { tokens } from "@enterprise-search/design-system";',
    )

    PRELUDE_LINES: ClassVar[tuple[str, ...]] = ("void tokens;",)

    @classmethod
    def build(
        cls,
        *,
        scheme: str,
        layout: LayoutTemplate,
        sample_state: SampleState,
        generated_at: str,
        generator_model: str,
    ) -> str:
        body = _LayoutSourceFactory.body_for(layout=layout, sample_state=sample_state)
        scheme_literal = _TsLiteral.from_string(scheme)
        matches_body = (
            "function matches(uri) { return typeof uri === "
            + _TsLiteral.from_string("string")
            + " && uri.indexOf("
            + scheme_literal
            + ") === 0; }"
        )
        metadata_object = (
            "{origin: "
            + _TsLiteral.from_string(_Origin.AGENT_GENERATED)
            + ", schemaVersion: "
            + str(_SchemaVersion.VALUE)
            + ", generatedAt: "
            + _TsLiteral.from_string(generated_at)
            + ", generatorModel: "
            + _TsLiteral.from_string(generator_model)
            + "}"
        )
        adapter_object = (
            "{scheme: "
            + scheme_literal
            + ", matches: matches, renderCurrent: renderCurrent, renderDiff: renderDiff,"
            " metadata: " + metadata_object + "}"
        )
        lines: list[str] = []
        lines.extend(cls.IMPORT_LINES)
        lines.append("")
        lines.extend(cls.PRELUDE_LINES)
        lines.append("")
        lines.append(matches_body)
        lines.append("")
        lines.append("export const renderCurrent = " + body.render_current + ";")
        lines.append("")
        lines.append("export const renderDiff = " + body.render_diff + ";")
        lines.append("")
        lines.append("export const adapter = " + adapter_object + ";")
        lines.append("")
        return "\n".join(lines)


class _LayoutBody:
    """Pair of arrow-function source strings for ``renderCurrent`` / ``renderDiff``."""

    def __init__(self, *, render_current: str, render_diff: str) -> None:
        self.render_current = render_current
        self.render_diff = render_diff


class _LayoutSourceFactory:
    """Route a layout choice to its dedicated source builder."""

    @classmethod
    def body_for(
        cls, *, layout: LayoutTemplate, sample_state: SampleState
    ) -> _LayoutBody:
        if layout is LayoutTemplate.FORM:
            return _FormBuilder.build(sample_state)
        if layout is LayoutTemplate.TABLE:
            return _TableBuilder.build(sample_state)
        if layout is LayoutTemplate.KANBAN:
            return _KanbanBuilder.build(sample_state)
        return _DefinitionListBuilder.build(sample_state)


class _SampleFieldExtractor:
    """Choose which sample-state fields each layout consumes."""

    DEFAULT_FIELDS: ClassVar[tuple[str, ...]] = (
        "id",
        "name",
        "title",
        "status",
        "owner",
        "amount",
        "stage",
        "updated_at",
    )

    @classmethod
    def primary_field_names(cls, sample_state: SampleState) -> list[str]:
        if sample_state.fields:
            return [str(name) for name in sample_state.fields]
        return list(cls.DEFAULT_FIELDS)

    @classmethod
    def first_string_field(cls, sample_state: SampleState) -> str:
        for name, value in sample_state.fields.items():
            if isinstance(value, str):
                return name
        return "id"

    @classmethod
    def row_list(cls, sample_state: SampleState) -> tuple[str, list[dict[str, object]]]:
        for name, value in sample_state.fields.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                rows: list[dict[str, object]] = []
                for entry in value:
                    if isinstance(entry, dict):
                        rows.append(entry)
                return name, rows
        return "rows", []

    @classmethod
    def column_names(cls, rows: list[dict[str, object]]) -> list[str]:
        seen: dict[str, None] = {}
        for row in rows:
            for key in row.keys():
                if isinstance(key, str) and key not in seen:
                    seen[key] = None
        if seen:
            return list(seen.keys())
        return ["id", "title", "status"]


class _FormBuilder:
    """``FORM`` layout: single-record header + bordered card with field rows."""

    @classmethod
    def build(cls, sample_state: SampleState) -> _LayoutBody:
        field_names = _SampleFieldExtractor.primary_field_names(sample_state)
        id_field = _SampleFieldExtractor.first_string_field(sample_state)

        field_row_calls_current = cls._field_rows("state", field_names)
        header_call = cls._header("state", id_field)
        card_call = _ReactCall.element(
            "div",
            props="{style: " + _StyleObject.CARD + "}",
            children=[header_call, *field_row_calls_current],
        )
        page_current = _ReactCall.element(
            "section",
            props="{style: "
            + _StyleObject.PAGE
            + ', "data-testid": "tier2-form-current"}',
            children=[card_call],
        )

        field_row_calls_diff = cls._diff_field_rows("diff", field_names)
        diff_header = cls._header("diff.base", id_field)
        diff_provenance = _ReactCall.element(
            "span",
            props="{style: " + _StyleObject.PROVENANCE_PILL + "}",
            children=[_ReactCall.expr_text('(diff && diff.provenance) || "PENDING"')],
        )
        diff_card = _ReactCall.element(
            "div",
            props="{style: " + _StyleObject.CARD + "}",
            children=[diff_header, diff_provenance, *field_row_calls_diff],
        )
        page_diff = _ReactCall.element(
            "section",
            props="{style: "
            + _StyleObject.PAGE
            + ', "data-testid": "tier2-form-diff"}',
            children=[diff_card],
        )

        return _LayoutBody(
            render_current="(state) => " + page_current,
            render_diff="(diff) => " + page_diff,
        )

    @classmethod
    def _header(cls, root_expr: str, id_field: str) -> str:
        title = _ReactCall.element(
            "span",
            props="{style: " + _StyleObject.HEADER_TITLE + "}",
            children=[_ReactCall.text("Record")],
        )
        id_value = _ReactCall.element(
            "span",
            props="{style: " + _StyleObject.HEADER_ID + "}",
            children=[
                _ReactCall.expr_text(
                    "("
                    + root_expr
                    + " && "
                    + root_expr
                    + "["
                    + _TsLiteral.from_string(id_field)
                    + ']) || ""'
                ),
            ],
        )
        return _ReactCall.element(
            "header",
            props="{style: " + _StyleObject.HEADER + "}",
            children=[title, id_value],
        )

    @classmethod
    def _field_rows(cls, root_expr: str, field_names: list[str]) -> list[str]:
        rows: list[str] = []
        for field_name in field_names:
            label = _ReactCall.element(
                "span",
                props="{style: " + _StyleObject.FIELD_LABEL + "}",
                children=[_ReactCall.text(field_name)],
            )
            value_expr = _ReactCall.expr_text(
                "("
                + root_expr
                + " && "
                + root_expr
                + "["
                + _TsLiteral.from_string(field_name)
                + ']) ?? ""'
            )
            value = _ReactCall.element(
                "span",
                props="{style: " + _StyleObject.FIELD_VALUE + "}",
                children=[value_expr],
            )
            rows.append(
                _ReactCall.element(
                    "div",
                    props="{key: "
                    + _TsLiteral.from_string(field_name)
                    + ", style: "
                    + _StyleObject.FIELD_ROW
                    + "}",
                    children=[label, value],
                )
            )
        return rows

    @classmethod
    def _diff_field_rows(cls, diff_expr: str, field_names: list[str]) -> list[str]:
        rows: list[str] = []
        for field_name in field_names:
            literal = _TsLiteral.from_string(field_name)
            base_expr = (
                "("
                + diff_expr
                + " && "
                + diff_expr
                + ".base && "
                + diff_expr
                + ".base["
                + literal
                + ']) ?? ""'
            )
            pending_expr = (
                "("
                + diff_expr
                + " && "
                + diff_expr
                + ".pending && "
                + diff_expr
                + ".pending["
                + literal
                + ']) ?? ""'
            )
            changed_expr = (
                _ReactCall.expr_text(base_expr)
                + " !== "
                + _ReactCall.expr_text(pending_expr)
            )
            label = _ReactCall.element(
                "span",
                props="{style: " + _StyleObject.FIELD_LABEL + "}",
                children=[_ReactCall.text(field_name)],
            )
            old_value = _ReactCall.element(
                "span",
                props="{style: " + _StyleObject.OLD_VALUE + "}",
                children=[_ReactCall.expr_text(base_expr)],
            )
            new_value = _ReactCall.element(
                "span",
                props="{style: " + _StyleObject.NEW_VALUE + "}",
                children=[_ReactCall.expr_text(pending_expr)],
            )
            pair = _ReactCall.element(
                "div",
                props="{style: " + _StyleObject.OLD_NEW_PAIR + "}",
                children=[old_value, new_value],
            )
            row_style = (
                "("
                + changed_expr
                + ") ? "
                + _StyleObject.PENDING_FIELD
                + " : "
                + _StyleObject.FIELD_ROW
            )
            rows.append(
                _ReactCall.element(
                    "div",
                    props="{key: " + literal + ", style: " + row_style + "}",
                    children=[label, pair],
                )
            )
        return rows


class _TableBuilder:
    """``TABLE`` layout: sticky-header grid view for list-shaped resources."""

    @classmethod
    def build(cls, sample_state: SampleState) -> _LayoutBody:
        rows_field, sample_rows = _SampleFieldExtractor.row_list(sample_state)
        columns = _SampleFieldExtractor.column_names(sample_rows)

        header_cells = [
            _ReactCall.element(
                "th",
                props="{key: "
                + _TsLiteral.from_string(col)
                + ", style: "
                + _StyleObject.TABLE_HEAD
                + "}",
                children=[_ReactCall.text(col)],
            )
            for col in columns
        ]
        thead = _ReactCall.element(
            "thead",
            props="null",
            children=[
                _ReactCall.element(
                    "tr",
                    props="null",
                    children=header_cells,
                )
            ],
        )

        body_call_current = cls._tbody_current(rows_field, columns)
        body_call_diff = cls._tbody_diff(rows_field, columns)

        table_current = _ReactCall.element(
            "table",
            props="{style: " + _StyleObject.TABLE + "}",
            children=[thead, body_call_current],
        )
        page_current = _ReactCall.element(
            "section",
            props="{style: "
            + _StyleObject.PAGE
            + ', "data-testid": "tier2-table-current"}',
            children=[table_current],
        )

        table_diff = _ReactCall.element(
            "table",
            props="{style: " + _StyleObject.TABLE + "}",
            children=[thead, body_call_diff],
        )
        page_diff = _ReactCall.element(
            "section",
            props="{style: "
            + _StyleObject.PAGE
            + ', "data-testid": "tier2-table-diff"}',
            children=[table_diff],
        )

        return _LayoutBody(
            render_current="(state) => " + page_current,
            render_diff="(diff) => " + page_diff,
        )

    @classmethod
    def _tbody_current(cls, rows_field: str, columns: list[str]) -> str:
        rows_expr = (
            "((state && state["
            + _TsLiteral.from_string(rows_field)
            + "]) || []).map(function (row, rowIndex) { "
            "return "
            + _ReactCall.element(
                "tr",
                props="{key: rowIndex}",
                children=[cls._cell_for_column(col, expr="row") for col in columns],
            )
            + "; })"
        )
        return _ReactCall.element("tbody", props="null", children=[rows_expr])

    @classmethod
    def _tbody_diff(cls, rows_field: str, columns: list[str]) -> str:
        rows_expr = (
            "((diff && diff.rows) || []).map(function (entry, rowIndex) { "
            "var current = (entry && entry.current) || {}; var previous = (entry && entry.previous) || {}; "
            "return "
            + _ReactCall.element(
                "tr",
                props="{key: rowIndex}",
                children=[cls._cell_for_diff_column(col) for col in columns],
            )
            + "; })"
        )
        return _ReactCall.element("tbody", props="null", children=[rows_expr])

    @classmethod
    def _cell_for_column(cls, col: str, *, expr: str) -> str:
        literal = _TsLiteral.from_string(col)
        return _ReactCall.element(
            "td",
            props="{key: " + literal + ", style: " + _StyleObject.TABLE_CELL + "}",
            children=[
                _ReactCall.expr_text(
                    "(" + expr + " && " + expr + "[" + literal + ']) ?? ""'
                ),
            ],
        )

    @classmethod
    def _cell_for_diff_column(cls, col: str) -> str:
        literal = _TsLiteral.from_string(col)
        changed = (
            "("
            + _ReactCall.expr_text("current[" + literal + '] ?? ""')
            + " !== "
            + _ReactCall.expr_text("previous[" + literal + '] ?? ""')
            + ")"
        )
        return _ReactCall.element(
            "td",
            props=(
                "{key: "
                + literal
                + ", style: "
                + changed
                + " ? "
                + _StyleObject.TABLE_CELL_CHANGED
                + " : "
                + _StyleObject.TABLE_CELL
                + "}"
            ),
            children=[
                _ReactCall.expr_text("current[" + literal + '] ?? ""'),
            ],
        )


class _KanbanBuilder:
    """``KANBAN`` layout: status-column board for collections of cards."""

    DEFAULT_STATUSES: ClassVar[tuple[str, ...]] = (
        "todo",
        "in_progress",
        "done",
    )

    @classmethod
    def build(cls, sample_state: SampleState) -> _LayoutBody:
        statuses = cls._statuses(sample_state)
        statuses_literal = (
            "[" + ", ".join(_TsLiteral.from_string(s) for s in statuses) + "]"
        )

        column_factory_current = (
            statuses_literal + ".map(function (status) { "
            "var cards = ((state && state.cards) || []).filter(function (card) { "
            "return card && card.status === status; }); "
            "return "
            + _ReactCall.element(
                "div",
                props="{key: status, style: " + _StyleObject.KANBAN_COLUMN + "}",
                children=[
                    _ReactCall.element(
                        "div",
                        props="{style: " + _StyleObject.KANBAN_COL_HEADER + "}",
                        children=[_ReactCall.expr_text("status")],
                    ),
                    cls._card_list_current(),
                ],
            )
            + "; })"
        )

        column_factory_diff = (
            statuses_literal + ".map(function (status) { "
            "var entries = ((diff && diff.cards) || []).filter(function (entry) { "
            "return entry && entry.current && entry.current.status === status; }); "
            "return "
            + _ReactCall.element(
                "div",
                props="{key: status, style: " + _StyleObject.KANBAN_COLUMN + "}",
                children=[
                    _ReactCall.element(
                        "div",
                        props="{style: " + _StyleObject.KANBAN_COL_HEADER + "}",
                        children=[_ReactCall.expr_text("status")],
                    ),
                    cls._card_list_diff(),
                ],
            )
            + "; })"
        )

        board_current = _ReactCall.element(
            "div",
            props="{style: " + _StyleObject.KANBAN_BOARD + "}",
            children=[column_factory_current],
        )
        page_current = _ReactCall.element(
            "section",
            props="{style: "
            + _StyleObject.PAGE
            + ', "data-testid": "tier2-kanban-current"}',
            children=[board_current],
        )

        board_diff = _ReactCall.element(
            "div",
            props="{style: " + _StyleObject.KANBAN_BOARD + "}",
            children=[column_factory_diff],
        )
        page_diff = _ReactCall.element(
            "section",
            props="{style: "
            + _StyleObject.PAGE
            + ', "data-testid": "tier2-kanban-diff"}',
            children=[board_diff],
        )

        return _LayoutBody(
            render_current="(state) => " + page_current,
            render_diff="(diff) => " + page_diff,
        )

    @classmethod
    def _statuses(cls, sample_state: SampleState) -> tuple[str, ...]:
        seen: dict[str, None] = {}
        for value in sample_state.fields.values():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        status = item.get("status")
                        if isinstance(status, str) and status.strip():
                            seen[status.strip()] = None
        if seen:
            return tuple(seen.keys())
        return cls.DEFAULT_STATUSES

    @classmethod
    def _card_list_current(cls) -> str:
        body = (
            "cards.map(function (card, cardIndex) { return "
            + _ReactCall.element(
                "div",
                props="{key: cardIndex, style: " + _StyleObject.KANBAN_CARD + "}",
                children=[
                    _ReactCall.element(
                        "span",
                        props="{style: " + _StyleObject.FIELD_VALUE + "}",
                        children=[_ReactCall.expr_text('(card && card.title) || ""')],
                    ),
                    _ReactCall.element(
                        "span",
                        props="{style: " + _StyleObject.HEADER_TITLE + "}",
                        children=[_ReactCall.expr_text('(card && card.owner) || ""')],
                    ),
                ],
            )
            + "; })"
        )
        return body

    @classmethod
    def _card_list_diff(cls) -> str:
        body = (
            "entries.map(function (entry, entryIndex) { "
            "var card = (entry && entry.current) || {}; var previous = (entry && entry.previous) || {}; "
            "var changed = card.status !== previous.status || card.title !== previous.title; "
            "return "
            + _ReactCall.element(
                "div",
                props=(
                    "{key: entryIndex, style: changed ? "
                    + _StyleObject.KANBAN_CARD_CHANGED
                    + " : "
                    + _StyleObject.KANBAN_CARD
                    + "}"
                ),
                children=[
                    _ReactCall.element(
                        "span",
                        props="{style: " + _StyleObject.FIELD_VALUE + "}",
                        children=[_ReactCall.expr_text('(card && card.title) || ""')],
                    ),
                    _ReactCall.element(
                        "span",
                        props="{style: " + _StyleObject.HEADER_TITLE + "}",
                        children=[_ReactCall.expr_text('(card && card.owner) || ""')],
                    ),
                ],
            )
            + "; })"
        )
        return body


class _DefinitionListBuilder:
    """``DEFINITION_LIST`` layout: generic key/value resource view."""

    @classmethod
    def build(cls, sample_state: SampleState) -> _LayoutBody:
        field_names = _SampleFieldExtractor.primary_field_names(sample_state)

        children_current: list[str] = []
        for field_name in field_names:
            literal = _TsLiteral.from_string(field_name)
            dt = _ReactCall.element(
                "dt",
                props="{key: "
                + _TsLiteral.from_string(field_name + ":label")
                + ", style: "
                + _StyleObject.DT
                + "}",
                children=[_ReactCall.text(field_name)],
            )
            dd = _ReactCall.element(
                "dd",
                props="{key: "
                + _TsLiteral.from_string(field_name + ":value")
                + ", style: "
                + _StyleObject.DD
                + "}",
                children=[
                    _ReactCall.expr_text("(state && state[" + literal + ']) ?? ""'),
                ],
            )
            children_current.append(dt)
            children_current.append(dd)
        dl_current = _ReactCall.element(
            "dl",
            props="{style: " + _StyleObject.DL + "}",
            children=children_current,
        )
        page_current = _ReactCall.element(
            "section",
            props="{style: "
            + _StyleObject.PAGE
            + ', "data-testid": "tier2-dl-current"}',
            children=[dl_current],
        )

        children_diff: list[str] = []
        for field_name in field_names:
            literal = _TsLiteral.from_string(field_name)
            base_expr = "(diff && diff.base && diff.base[" + literal + ']) ?? ""'
            pending_expr = (
                "(diff && diff.pending && diff.pending[" + literal + ']) ?? ""'
            )
            dt = _ReactCall.element(
                "dt",
                props="{key: "
                + _TsLiteral.from_string(field_name + ":label")
                + ", style: "
                + _StyleObject.DT
                + "}",
                children=[_ReactCall.text(field_name)],
            )
            old_span = _ReactCall.element(
                "span",
                props="{style: " + _StyleObject.OLD_VALUE + "}",
                children=[_ReactCall.expr_text(base_expr)],
            )
            arrow = _ReactCall.element(
                "span",
                props="{style: " + _StyleObject.HEADER_TITLE + "}",
                children=[_ReactCall.text("->")],
            )
            new_span = _ReactCall.element(
                "span",
                props="{style: " + _StyleObject.NEW_VALUE + "}",
                children=[_ReactCall.expr_text(pending_expr)],
            )
            dd = _ReactCall.element(
                "dd",
                props="{key: "
                + _TsLiteral.from_string(field_name + ":value")
                + ", style: "
                + _StyleObject.DD
                + "}",
                children=[old_span, arrow, new_span],
            )
            children_diff.append(dt)
            children_diff.append(dd)
        dl_diff = _ReactCall.element(
            "dl",
            props="{style: " + _StyleObject.DL + "}",
            children=children_diff,
        )
        page_diff = _ReactCall.element(
            "section",
            props="{style: " + _StyleObject.PAGE + ', "data-testid": "tier2-dl-diff"}',
            children=[dl_diff],
        )

        return _LayoutBody(
            render_current="(state) => " + page_current,
            render_diff="(diff) => " + page_diff,
        )


__all__ = [
    "AdapterSourceBuilder",
]

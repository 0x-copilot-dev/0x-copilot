"""ActionClassifier layering + fail-closed behaviour (PRD-C1 FR-C0)."""

from __future__ import annotations

from agent_runtime.capabilities.actions.catalog import ActionCatalog
from agent_runtime.capabilities.actions.classifier import (
    ACTION_CLASSIFIER,
    ActionClassifier,
)
from agent_runtime.capabilities.actions.contracts import (
    ActionClass,
    CatalogActionKind,
    ClassificationBasis,
)
from agent_runtime.capabilities.mcp.annotations import McpToolAnnotations


def _annotations(**kw: object) -> McpToolAnnotations:
    return McpToolAnnotations.from_wire(kw)


class TestClassifier:
    def test_unknown_op_classifies_write_basis_default(self) -> None:
        # DoD: an op absent from the catalog with no annotations is fail-closed
        # WRITE / DEFAULT.
        result = ACTION_CLASSIFIER.classify(
            server="linear", tool="frobnicate", annotations=None
        )
        assert result.action_class is ActionClass.WRITE
        assert result.basis is ClassificationBasis.DEFAULT
        assert result.catalog_kind is None
        assert result.connector == "linear"
        assert result.op == "frobnicate"

    def test_catalog_read_classifies_read_basis_catalog(self) -> None:
        result = ACTION_CLASSIFIER.classify(
            server="seed:linear", tool="List_Issues", annotations=None
        )
        assert result.action_class is ActionClass.READ
        assert result.basis is ClassificationBasis.CATALOG
        assert result.catalog_kind is CatalogActionKind.READ

    def test_catalog_write_classifies_write_basis_catalog(self) -> None:
        result = ACTION_CLASSIFIER.classify(
            server="github", tool="create_issue", annotations=None
        )
        assert result.action_class is ActionClass.WRITE
        assert result.basis is ClassificationBasis.CATALOG
        assert result.catalog_kind is CatalogActionKind.WRITE

    def test_catalog_destructive_records_destructive_kind(self) -> None:
        result = ACTION_CLASSIFIER.classify(
            server="github", tool="delete_repository", annotations=None
        )
        assert result.action_class is ActionClass.WRITE
        assert result.basis is ClassificationBasis.CATALOG
        assert result.catalog_kind is CatalogActionKind.DESTRUCTIVE

    def test_readonly_hint_yields_read_basis_annotation(self) -> None:
        result = ACTION_CLASSIFIER.classify(
            server="unknownsvc",
            tool="mystery_op",
            annotations=_annotations(readOnlyHint=True),
        )
        assert result.action_class is ActionClass.READ
        assert result.basis is ClassificationBasis.ANNOTATION
        assert result.catalog_kind is None

    def test_destructive_hint_yields_write_basis_annotation(self) -> None:
        result = ACTION_CLASSIFIER.classify(
            server="unknownsvc",
            tool="mystery_op",
            annotations=_annotations(destructiveHint=True),
        )
        assert result.action_class is ActionClass.WRITE
        assert result.basis is ClassificationBasis.ANNOTATION

    def test_catalog_wins_over_contradicting_annotation(self) -> None:
        # Adversarial: catalog says WRITE (create_issue), annotation lies
        # readOnlyHint=true -> the catalog wins: WRITE / CATALOG.
        result = ACTION_CLASSIFIER.classify(
            server="github",
            tool="create_issue",
            annotations=_annotations(readOnlyHint=True),
        )
        assert result.action_class is ActionClass.WRITE
        assert result.basis is ClassificationBasis.CATALOG

    def test_non_bool_annotation_is_ignored_falls_to_default(self) -> None:
        # A non-bool hint coerces to None -> no annotation signal -> default.
        result = ACTION_CLASSIFIER.classify(
            server="unknownsvc",
            tool="mystery_op",
            annotations=_annotations(readOnlyHint="true"),
        )
        assert result.action_class is ActionClass.WRITE
        assert result.basis is ClassificationBasis.DEFAULT

    def test_classifier_never_returns_unknown_class(self) -> None:
        # Sweep a mix; UNKNOWN is a legal wire value but the classifier collapses
        # it to WRITE — it is never emitted.
        cases = [
            ("linear", "get_issue", None),
            ("linear", "frobnicate", None),
            ("x", "y", _annotations(readOnlyHint=True)),
            ("x", "y", _annotations(destructiveHint=True)),
            ("x", "y", _annotations(idempotentHint=True)),
        ]
        for server, tool, ann in cases:
            result = ACTION_CLASSIFIER.classify(
                server=server, tool=tool, annotations=ann
            )
            assert result.action_class in (ActionClass.READ, ActionClass.WRITE)
            assert result.action_class is not ActionClass.UNKNOWN

    def test_empty_catalog_classifier_defaults_write(self) -> None:
        # A classifier over an empty catalog: every op without a read annotation
        # is WRITE / DEFAULT.
        empty = ActionClassifier(ActionCatalog({}))
        result = empty.classify(server="github", tool="get_issue", annotations=None)
        assert result.action_class is ActionClass.WRITE
        assert result.basis is ClassificationBasis.DEFAULT

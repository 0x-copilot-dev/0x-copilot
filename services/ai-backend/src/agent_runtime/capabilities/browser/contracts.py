"""Authority contracts for the desktop browser operation adapter.

The browser process owns cookies, profile state, native dialogs, and page handles.
This module is deliberately the other side of that boundary: it carries only
opaque scoped references, bounded safe summaries, and immutable action-plan
facts.  It has no HTTP credential, cookie, selector, JavaScript, or host-path
field by design.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from agent_runtime.capabilities.operations.contracts import (
    ArtifactPublicationSource,
    OperationRawResult,
    ProposedEffect,
)
from agent_runtime.execution.contracts import RuntimeContract
from agent_runtime.surfaces_v2.canonical_json import canonical_json_bytes, sha256_hex
from agent_runtime.surfaces_v2.entities import OperationRequest
from agent_runtime.surfaces_v2.ledger_models import Sha256Hex

_REF_MAX = 2048
_SUMMARY_MAX = 512


class BrowserActionKind(StrEnum):
    """Closed consequential browser action vocabulary.

    ``click`` remains intentionally broad: a model cannot relabel an ambiguous
    click as a read.  The Electron bridge may support a smaller cohort than this
    vocabulary, but it must never reinterpret an unknown action as safe.
    """

    CLICK = "click"
    INPUT = "input"
    SELECT = "select"
    SUBMIT = "submit"
    UPLOAD_SUBMIT = "upload_submit"


class BrowserApplyOutcome(StrEnum):
    APPLIED = "applied"
    PRECONDITION_DRIFT = "precondition_drift"
    FAILED = "failed"
    INDETERMINATE = "indeterminate"


def _opaque_ref(value: str, field_name: str) -> str:
    """Reject host paths, URLs, traversal, and secret-shaped transports."""

    if (
        not isinstance(value, str)
        or not value
        or len(value) > _REF_MAX
        or value != value.strip()
        or "\n" in value
        or "\r" in value
        or "\x00" in value
        or value.startswith(("/", "~", "\\"))
        or (len(value) >= 3 and value[1:3] in {":/", ":\\"})
    ):
        raise ValueError(f"{field_name} must be an opaque scoped reference")
    parsed = urlsplit(value)
    if (
        not parsed.scheme
        or not parsed.netloc
        or parsed.query
        or parsed.fragment
        or parsed.scheme.lower() in {"file", "filesystem", "data", "http", "https"}
        or parsed.path.startswith("//")
        or any(part in {".", ".."} for part in (parsed.netloc, *parsed.path.split("/")))
    ):
        raise ValueError(f"{field_name} must be an opaque scoped reference")
    return value


def _canonical_origin(value: str, field_name: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or value != f"https://{parsed.netloc.lower()}"
    ):
        raise ValueError(f"{field_name} must be a canonical https origin")
    return value


class BrowserPrecondition(RuntimeContract):
    """The observed page identity a browser approval binds to exactly."""

    page_generation: int = Field(ge=0)
    origin: str = Field(min_length=1, max_length=512)
    element_fingerprint: Sha256Hex | None = None
    form_fingerprint: Sha256Hex | None = None

    @field_validator("origin")
    @classmethod
    def _origin_is_canonical(cls, value: str) -> str:
        return _canonical_origin(value, "origin")

    @property
    def digest(self) -> str:
        return sha256_hex(canonical_json_bytes(self.model_dump(mode="json")))


class BrowserUploadArtifact(RuntimeContract):
    """Server-authorized immutable upload material for one browser plan.

    The model may name an artifact revision, but only an A2-backed authorizer
    may resolve it into this digest-pinned metadata. Browser code therefore
    never accepts a local path or infers upload bytes from a filename.
    """

    artifact_ref: str = Field(min_length=1, max_length=_REF_MAX)
    digest: Sha256Hex
    byte_size: int = Field(ge=0)
    media_type: str = Field(min_length=1, max_length=255)
    suggested_filename: str = Field(min_length=1, max_length=255)

    @field_validator("artifact_ref")
    @classmethod
    def _artifact_ref_is_opaque(cls, value: str) -> str:
        _opaque_ref(value, "upload artifact reference")
        if not value.startswith("artifact://"):
            raise ValueError("upload source must be an artifact revision reference")
        return value

    @field_validator("suggested_filename")
    @classmethod
    def _filename_is_basename(cls, value: str) -> str:
        if (
            value in {".", ".."}
            or value != value.strip()
            or "/" in value
            or "\\" in value
            or "\x00" in value
        ):
            raise ValueError("upload filename must be safe metadata")
        return value


class BrowserActionPlan(RuntimeContract):
    """Exact, body-free plan persisted before any browser external action."""

    session_ref: str = Field(min_length=1, max_length=_REF_MAX)
    page_ref: str = Field(min_length=1, max_length=_REF_MAX)
    origin: str = Field(min_length=1, max_length=512)
    top_level_origin: str = Field(min_length=1, max_length=512)
    action_kind: BrowserActionKind
    element_ref: str | None = Field(default=None, min_length=1, max_length=255)
    element_fingerprint: Sha256Hex | None = None
    form_action_url: str | None = Field(default=None, min_length=1, max_length=2048)
    method: str | None = Field(default=None, min_length=1, max_length=16)
    canonical_fields_ref: str = Field(min_length=1, max_length=_REF_MAX)
    fields_digest: Sha256Hex
    upload_artifact_refs: tuple[str, ...] = Field(default_factory=tuple, max_length=32)
    upload_artifacts: tuple[BrowserUploadArtifact, ...] = Field(
        default_factory=tuple,
        max_length=32,
    )
    precondition: BrowserPrecondition
    precondition_digest: Sha256Hex
    user_visible_summary: str = Field(min_length=1, max_length=_SUMMARY_MAX)

    @field_validator("session_ref", "page_ref", "canonical_fields_ref")
    @classmethod
    def _refs_are_opaque(cls, value: str) -> str:
        return _opaque_ref(value, "browser reference")

    @field_validator("origin", "top_level_origin")
    @classmethod
    def _origins_are_canonical(cls, value: str) -> str:
        return _canonical_origin(value, "browser origin")

    @field_validator("form_action_url")
    @classmethod
    def _form_url_is_https(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.fragment
            or "\x00" in value
        ):
            raise ValueError("form_action_url must be an https URL without credentials")
        return value

    @field_validator("method")
    @classmethod
    def _method_is_known(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.upper()
        if normalized not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ValueError("method must be a recognized HTTP method")
        return normalized

    @field_validator("upload_artifact_refs")
    @classmethod
    def _uploads_are_opaque(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        seen: set[str] = set()
        for value in values:
            _opaque_ref(value, "upload artifact reference")
            if not value.startswith("artifact://"):
                raise ValueError("upload source must be an artifact revision reference")
            if value in seen:
                raise ValueError("upload artifact references must be unique")
            seen.add(value)
        return values

    @model_validator(mode="after")
    def _plan_is_complete(self) -> BrowserActionPlan:
        if self.action_kind in {
            BrowserActionKind.CLICK,
            BrowserActionKind.INPUT,
            BrowserActionKind.SELECT,
            BrowserActionKind.SUBMIT,
            BrowserActionKind.UPLOAD_SUBMIT,
        } and (self.element_ref is None or self.element_fingerprint is None):
            raise ValueError(
                "browser action requires an exact element reference and fingerprint"
            )
        upload_refs = tuple(upload.artifact_ref for upload in self.upload_artifacts)
        if upload_refs != self.upload_artifact_refs:
            raise ValueError(
                "upload authorization must bind every exact artifact revision"
            )
        if self.action_kind is BrowserActionKind.UPLOAD_SUBMIT and not upload_refs:
            raise ValueError("upload_submit requires at least one artifact revision")
        if self.precondition.origin != self.origin:
            raise ValueError("browser precondition origin must match action origin")
        if self.element_fingerprint != self.precondition.element_fingerprint:
            raise ValueError("browser plan fingerprint must match its precondition")
        if self.precondition_digest != self.precondition.digest:
            raise ValueError(
                "browser precondition digest must bind the exact precondition"
            )
        return self

    @property
    def digest(self) -> str:
        return sha256_hex(canonical_json_bytes(self.model_dump(mode="json")))

    @property
    def target_digest(self) -> str:
        return sha256_hex(
            canonical_json_bytes(
                {
                    "session_ref": self.session_ref,
                    "page_ref": self.page_ref,
                    "origin": self.origin,
                    "top_level_origin": self.top_level_origin,
                    "action_kind": self.action_kind.value,
                    "element_ref": self.element_ref,
                    "element_fingerprint": self.element_fingerprint,
                    "form_action_url": self.form_action_url,
                    "method": self.method,
                    "upload_artifact_refs": list(self.upload_artifact_refs),
                    "upload_artifact_digests": [
                        upload.digest for upload in self.upload_artifacts
                    ],
                }
            )
        )


class BrowserReadRequest(RuntimeContract):
    """Safe gateway-to-browser read request; it cannot name a host path."""

    operation_id: str = Field(min_length=1, max_length=255)
    run_id: str = Field(min_length=1, max_length=255)
    op: str = Field(min_length=1, max_length=128)
    arguments: dict[str, object]


@dataclass(frozen=True)
class BrowserArtifactPayload:
    """Exact private-browser bytes awaiting A2 publication.

    Bytes are intentionally non-repr so a failed assertion or log line cannot
    leak a downloaded document.  They never include a host path.
    """

    content: bytes = field(repr=False)
    digest: str
    byte_size: int
    media_type: str
    suggested_filename: str | None
    source_ref: str

    def __post_init__(self) -> None:
        if (
            sha256_hex(self.content) != self.digest
            or len(self.content) != self.byte_size
        ):
            raise ValueError("browser artifact payload digest or size mismatch")
        _opaque_ref(self.source_ref, "browser artifact source")
        if self.suggested_filename is not None and (
            not self.suggested_filename
            or "/" in self.suggested_filename
            or "\\" in self.suggested_filename
            or "\x00" in self.suggested_filename
        ):
            raise ValueError("browser artifact filename must be a basename")


class BrowserReadResult(RuntimeContract):
    """Bounded browser read metadata. Artifact bytes stay on the private port."""

    safe_summary: str = Field(min_length=1, max_length=_SUMMARY_MAX)
    result_ref: str | None = Field(default=None, max_length=_REF_MAX)
    activity_ref: str | None = Field(default=None, max_length=_REF_MAX)

    @field_validator("result_ref", "activity_ref")
    @classmethod
    def _logical_refs(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _opaque_ref(value, "browser read result reference")


class BrowserPrepareResult(RuntimeContract):
    """Private Electron-main outcome of checking an exact action plan."""

    prepared_ref: str | None = Field(default=None, max_length=_REF_MAX)
    observed_precondition_digest: Sha256Hex
    expires_at: str | None = Field(default=None, max_length=64)
    precondition_drift: bool = False

    @field_validator("prepared_ref")
    @classmethod
    def _prepared_ref_is_opaque(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _opaque_ref(value, "prepared browser action reference")

    @model_validator(mode="after")
    def _prepared_or_drift(self) -> BrowserPrepareResult:
        if self.precondition_drift and self.prepared_ref is not None:
            raise ValueError(
                "drifted browser action must not have a prepared reference"
            )
        if not self.precondition_drift and self.prepared_ref is None:
            raise ValueError("prepared browser action requires a prepared reference")
        return self


class BrowserApplyReceipt(RuntimeContract):
    """Safe browser executor receipt; no cookies, bodies, or native paths."""

    outcome: BrowserApplyOutcome
    receipt_ref: str | None = Field(default=None, max_length=_REF_MAX)
    result_digest: Sha256Hex | None = None
    safe_message: str | None = Field(default=None, max_length=_SUMMARY_MAX)

    @field_validator("receipt_ref")
    @classmethod
    def _receipt_ref_is_opaque(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _opaque_ref(value, "browser receipt reference")


class BrowserStoredPlan(RuntimeContract):
    """One immutable action plan persisted through an A2/A5-owned store."""

    content_ref: str = Field(min_length=1, max_length=_REF_MAX)
    digest: Sha256Hex

    @field_validator("content_ref")
    @classmethod
    def _content_ref_is_opaque(cls, value: str) -> str:
        return _opaque_ref(value, "browser action plan content reference")


@runtime_checkable
class BrowserPrivateBridge(Protocol):
    """Electron-main-only authority port.

    Its implementation owns the authenticated desktop channel, browser profile,
    cookies, and OS prompts.  Python receives no transport credential and never
    receives an arbitrary selector or host path.
    """

    async def execute_read(self, request: BrowserReadRequest) -> BrowserReadResult:
        """Run one bounded read/internal browser operation."""

    async def artifact_payload(
        self, *, operation_id: str
    ) -> BrowserArtifactPayload | None:
        """Consume exact private bytes captured by a read/download operation."""

    async def prepare_action(self, plan: BrowserActionPlan) -> BrowserPrepareResult:
        """Revalidate session/page/origin/element without a side effect."""

    async def apply_prepared(self, prepared_ref: str) -> BrowserApplyReceipt:
        """Apply exactly one prepared plan after A5 has durably claimed it."""

    async def reconcile_action(self, prepared_ref: str) -> BrowserApplyReceipt:
        """Observe one prior attempt; it must never re-send a browser action."""


@runtime_checkable
class BrowserActionPlanStore(Protocol):
    """Persist exact canonical plan bytes under an immutable server-owned ref."""

    async def store(self, *, plan: BrowserActionPlan) -> BrowserStoredPlan:
        """Store one plan exactly once and return its immutable content locator."""

    async def load(self, *, content_ref: str) -> BrowserActionPlan | None:
        """Load a previously persisted plan for A5 executor preparation."""


@runtime_checkable
class BrowserUploadAuthorizer(Protocol):
    """A2 authorization seam for immutable browser upload revisions.

    Implementations enforce bound run/org/user scope before returning metadata.
    They do not expose the body to model-visible plan data; a later Electron
    upload bridge streams it only after the exact action has prepared.
    """

    async def authorize(
        self,
        *,
        request: OperationRequest,
        artifact_refs: tuple[str, ...],
    ) -> tuple[BrowserUploadArtifact, ...]:
        """Resolve only artifact revisions authorized for this operation."""


@runtime_checkable
class BrowserStagePort(Protocol):
    """A4/A5 staging seam; no browser execution capability is exposed here."""

    async def stage(
        self,
        *,
        request: OperationRequest,
        plan: BrowserActionPlan,
    ) -> ProposedEffect:
        """Persist one exact held browser proposal through the effect stager."""


class BrowserOperationError(RuntimeError):
    """Safe adapter failure. The gateway maps it to a non-effectful outcome."""


def artifact_publication_from_payload(
    payload: BrowserArtifactPayload,
) -> ArtifactPublicationSource:
    """Convert private bytes into the narrow A3 publication hand-off."""

    return ArtifactPublicationSource(
        content=payload.content,
        source_ref=payload.source_ref,
    )


def read_result_from_browser(result: BrowserReadResult) -> OperationRawResult:
    """Map an Electron-safe result into the universal gateway result contract."""

    return OperationRawResult(
        result_ref=result.result_ref,
        activity_ref=result.activity_ref,
        safe_summary=result.safe_summary,
    )


__all__ = (
    "BrowserActionKind",
    "BrowserActionPlan",
    "BrowserActionPlanStore",
    "BrowserApplyOutcome",
    "BrowserApplyReceipt",
    "BrowserArtifactPayload",
    "BrowserOperationError",
    "BrowserPrecondition",
    "BrowserPrepareResult",
    "BrowserPrivateBridge",
    "BrowserReadRequest",
    "BrowserReadResult",
    "BrowserStagePort",
    "BrowserStoredPlan",
    "BrowserUploadArtifact",
    "BrowserUploadAuthorizer",
    "artifact_publication_from_payload",
    "read_result_from_browser",
)

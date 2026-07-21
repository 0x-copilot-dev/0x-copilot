"""Generative-UI surface capability package.

PRD-01 seeded this package with the SurfaceSpec pydantic mirror + validator
(:mod:`spec_models`). PRD-02 adds backend **emission**: a builtin curated spec
library (:mod:`builtin`), the pure-domain :class:`~.projector.SurfaceProjector`
that turns tool output into a ``SurfaceEnvelope``, and the
``RUNTIME_SURFACE_EMISSION`` flag (:mod:`config`). Renderers, the spec
generator, and the spec-authoring skill land in later waves.
"""

from agent_runtime.capabilities.surfaces.config import SurfaceEmissionFlag
from agent_runtime.capabilities.surfaces.projector import (
    InMemorySurfaceSpecStore,
    SurfaceProjector,
    SurfaceSpecStorePort,
)

__all__ = [
    "InMemorySurfaceSpecStore",
    "SurfaceEmissionFlag",
    "SurfaceProjector",
    "SurfaceSpecStorePort",
]

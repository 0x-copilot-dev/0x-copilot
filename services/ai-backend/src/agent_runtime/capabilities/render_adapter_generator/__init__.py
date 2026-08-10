"""Tier-2 render adapter generator capability (Phase 6B).

Produces sandbox-safe ``SaaSRendererAdapter`` source code from one of four
constrained layout templates, then emits an ``adapter_generated`` run event so
the desktop's tier-2 lifecycle (6C) can persist + install the result.
"""

from agent_runtime.capabilities.render_adapter_generator.capability import (
    AdapterAllowlistAuditor,
    AdapterCodegenError,
    RenderAdapterGenerator,
)
from agent_runtime.capabilities.render_adapter_generator.config import (
    Tier2GenerationFlag,
    should_invoke_tier2_generator,
)
from agent_runtime.capabilities.render_adapter_generator.models import (
    AdapterCodegenRequest,
    AdapterCodegenResult,
    LayoutTemplate,
    SampleState,
)

__all__ = [
    "AdapterAllowlistAuditor",
    "AdapterCodegenError",
    "AdapterCodegenRequest",
    "AdapterCodegenResult",
    "LayoutTemplate",
    "RenderAdapterGenerator",
    "SampleState",
    "Tier2GenerationFlag",
    "should_invoke_tier2_generator",
]

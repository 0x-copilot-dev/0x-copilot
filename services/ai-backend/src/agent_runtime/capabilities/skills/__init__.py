"""Skills middleware contracts and helpers."""

from agent_runtime.capabilities.skills.manifest import (
    MAX_SKILL_DESCRIPTION_LENGTH,
    MAX_SKILL_FILE_BYTES,
    SkillErrorCode,
    SkillManifest,
    SkillManifestParser,
    SkillManifestReader,
    SkillManifestError,
)
from agent_runtime.capabilities.skills.sources import (
    ConfiguredSkill,
    SkillSource,
    SkillSourceConfig,
    SkillSourceRegistry,
    SkillSourceScope,
)

# ``usage`` and ``visibility`` are deliberately NOT re-exported here.
# ``execution/contracts.py`` imports ``skills.sources``, so this package's
# ``__init__`` runs while ``execution.contracts`` is still initialising; both
# modules depend on ``RuntimeContract`` and re-exporting them would close that
# cycle at import time. Import them by module path
# (``agent_runtime.capabilities.skills.visibility``), the same way ``virtual``
# and ``middleware`` are already imported.

__all__ = [
    "ConfiguredSkill",
    "MAX_SKILL_DESCRIPTION_LENGTH",
    "MAX_SKILL_FILE_BYTES",
    "SkillErrorCode",
    "SkillManifest",
    "SkillManifestError",
    "SkillManifestParser",
    "SkillManifestReader",
    "SkillSource",
    "SkillSourceConfig",
    "SkillSourceRegistry",
    "SkillSourceScope",
]

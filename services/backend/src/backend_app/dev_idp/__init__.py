"""Dev identity issuer (W0.1).

Routes are registered iff ``BACKEND_ENVIRONMENT=development``. The module
exists in production builds but its routes are not added to the FastAPI app
— prod-build CI verifies they 404.
"""

from backend_app.dev_idp.personas import (
    DevOrg,
    DevPersona,
    PersonaDirectory,
)
from backend_app.dev_idp.routes import register_dev_idp_routes

__all__ = (
    "DevOrg",
    "DevPersona",
    "PersonaDirectory",
    "register_dev_idp_routes",
)

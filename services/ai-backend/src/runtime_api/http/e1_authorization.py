"""Narrow, reusable authorization checks for E1 read boundaries.

The persistence ports are tenant scoped but a run lookup itself is only scoped
by organization.  E1 routes that dereference a run therefore use this helper
to prove both the run owner and its parent conversation are still visible to
the verified caller.  Every denial deliberately collapses to the same 404 so
an opaque id is never treated as an authorization grant or enumeration oracle.
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from runtime_api.identity import RuntimeIdentity


class E1Authorization:
    """Ownership checks for the existing compatibility source feed and future routes."""

    NOT_FOUND_DETAIL = "resource not found"

    @classmethod
    def not_found(cls) -> HTTPException:
        """Return the one non-enumerating public response for this boundary."""

        return HTTPException(status.HTTP_404_NOT_FOUND, cls.NOT_FOUND_DETAIL)

    @classmethod
    async def require_owned_conversation(
        cls,
        request: Request,
        *,
        identity: RuntimeIdentity,
        conversation_id: str,
    ) -> object:
        """Return an owned parent conversation or a non-enumerable 404."""

        conversation = await request.app.state.runtime_persistence.get_conversation(
            org_id=identity.org_id,
            user_id=identity.user_id,
            conversation_id=conversation_id,
        )
        if conversation is None:
            raise cls.not_found()
        return conversation

    @classmethod
    async def require_owned_run(
        cls,
        request: Request,
        *,
        identity: RuntimeIdentity,
        run_id: str,
    ) -> object:
        """Return an owned run only after its parent membership is rechecked."""

        run = await request.app.state.runtime_persistence.get_run(
            org_id=identity.org_id,
            run_id=run_id,
        )
        if run is None or getattr(run, "user_id", None) != identity.user_id:
            raise cls.not_found()
        await cls.require_owned_conversation(
            request,
            identity=identity,
            conversation_id=str(getattr(run, "conversation_id", "")),
        )
        return run


__all__ = ("E1Authorization",)

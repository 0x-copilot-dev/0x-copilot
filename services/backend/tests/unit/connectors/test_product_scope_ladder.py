"""The product-scope ladder: which provider scopes a request actually asks for.

`write` was modelled from the start — `ProviderPermission.required_for` and
`ConnectorToolPolicy.product_scope` both accept it, and the tool validator has
a rule reserved for it — but the request surface stopped at `draft`. Google
Drive has shipped a `required_for: write` permission and two
`product_scope: write` tools since AC9 that nothing could ever ask for.

Two properties matter here, and they pull in opposite directions:

* the ladder is **cumulative**, so a connector granted `write` can read back
  what it just wrote; and
* an unrecognised scope resolves to `read` alone, so a caller that means more
  than it says gets less than it asked for.
"""

from __future__ import annotations

from backend_app.connectors.oauth_coordinator import DesktopMcpOAuthCoordinator
from backend_app.connectors.profile_catalog import DesktopProfileCatalog


# Bound method on the coordinator; the ladder is pure so no instance state
# is involved — call it unbound with an explicit `None` receiver.
def SCOPES(profile, scope):
    return DesktopMcpOAuthCoordinator._requested_permissions(None, profile, scope)


def _gdrive():
    """The one shipped profile that declares a write permission."""

    return DesktopProfileCatalog.load().get("gdrive")


class TestTheLadderIsCumulative:
    def test_read_asks_only_for_read(self) -> None:
        profile = _gdrive()
        granted = SCOPES(profile, "read")
        expected = {
            p.identifier for p in profile.permissions if p.required_for == "read"
        }
        assert set(granted) == expected

    def test_draft_includes_read(self) -> None:
        profile = _gdrive()
        granted = set(SCOPES(profile, "draft"))
        assert set(SCOPES(profile, "read")) <= granted

    def test_write_includes_draft_and_read(self) -> None:
        """A connector that can create a file must be able to read it back."""

        profile = _gdrive()
        granted = set(SCOPES(profile, "write"))
        assert set(SCOPES(profile, "draft")) <= granted
        assert set(SCOPES(profile, "read")) <= granted

    def test_write_now_reaches_the_permission_nothing_could_request(self) -> None:
        profile = _gdrive()
        write_only = {
            p.identifier for p in profile.permissions if p.required_for == "write"
        }
        assert write_only, "precondition: gdrive declares a write permission"
        # Before the ladder, `draft` was the ceiling and this scope was dead.
        assert not (write_only & set(SCOPES(profile, "draft")))
        assert write_only <= set(SCOPES(profile, "write"))


class TestItFailsClosed:
    def test_an_unknown_scope_falls_back_to_read(self) -> None:
        """Getting less than you asked for is the safe direction."""

        profile = _gdrive()
        assert set(SCOPES(profile, "superuser")) == set(SCOPES(profile, "read"))

    def test_an_empty_scope_falls_back_to_read(self) -> None:
        profile = _gdrive()
        assert set(SCOPES(profile, "")) == set(SCOPES(profile, "read"))

    def test_a_profile_with_no_write_permission_grants_none(self) -> None:
        """Widening the lane cannot invent a scope the profile never declared."""

        gmail = DesktopProfileCatalog.load().get("gmail")
        assert not any(p.required_for == "write" for p in gmail.permissions)
        assert set(SCOPES(gmail, "write")) == set(SCOPES(gmail, "draft"))


class TestTheSafetyGatesThatMakeWriteAcceptable:
    def test_every_write_tool_demands_per_call_approval(self) -> None:
        """The gate that makes widening the lane defensible.

        `ConnectorToolPolicy` refuses to load a draft/write tool that settles
        for session-scoped approval, so granting the scope does not grant
        unattended use of it.
        """

        for profile in DesktopProfileCatalog.load().profiles:
            for tool in profile.tools:
                if tool.product_scope in {"draft", "write"}:
                    assert tool.approval == "per_call", (
                        f"{profile.profile_id}:{tool.tool_name}"
                    )

    def test_the_shipped_write_scope_is_the_narrow_one(self) -> None:
        """`drive.file` reaches only files the app created or the user opened
        with it — not the user's Drive. If this ever widens to `drive`, the
        decision to allow write should be revisited, not inherited."""

        profile = _gdrive()
        write_scopes = {
            p.identifier for p in profile.permissions if p.required_for == "write"
        }
        assert write_scopes == {"https://www.googleapis.com/auth/drive.file"}

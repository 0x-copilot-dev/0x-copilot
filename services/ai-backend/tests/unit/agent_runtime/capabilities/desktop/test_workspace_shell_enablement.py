"""The per-workspace shell-enablement READ path (PRD-shell-execution §7.3).

The flag's whole journey on this side is: ``/v1/grants/snapshot`` →
:class:`BrokerGrant` → :class:`WorkspaceMount` → :class:`GrantedRoot`, which is
what §7.1's prerequisite 3 is checked against. Every test here is about the same
property said four ways:

    **OFF is what "we could not tell" means.** Absent from the wire, absent from
    an older record, dropped because the grant is revoked, or never mentioned by
    a caller that predates the field — each one arrives as ``False``. The only
    way to get ``True`` is for the wire to carry a literal JSON ``true`` on an
    active grant, which is only ever written by the Settings toggle.

Absence is not the exotic case: every workspace that exists today lacks the
field, so "absent" is the shape almost every real snapshot has.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_runtime.capabilities.desktop.broker_client import (
    BrokerGrant,
    BrokerGrantSnapshot,
)
from agent_runtime.capabilities.desktop.host_filesystem import GrantedRoot
from agent_runtime.capabilities.desktop.workspace_backend import (
    WorkspaceMount,
    WorkspaceMountTable,
)


def _grant(**overrides: object) -> BrokerGrant:
    payload: dict[str, object] = {
        "grantId": "grant_1",
        "mode": "read_write",
        "label": "my-project",
        "status": "active",
        "mount": "mnt_1",
        "root": "/Users/ada/code/my-project",
    }
    payload.update(overrides)
    return BrokerGrant.model_validate(payload)


class TestBrokerGrantDecode:
    """The wire boundary. ``extra="ignore"`` makes silence the common case."""

    def test_a_grant_with_no_flag_at_all_is_command_incapable(self) -> None:
        # THE COMMON CASE. Every grant minted before shell execution existed
        # looks exactly like this, and so does every grant sent by an Electron
        # main that predates the field.
        assert _grant().shell_enabled is False

    def test_an_explicit_false_is_command_incapable(self) -> None:
        assert _grant(shellEnabled=False).shell_enabled is False

    def test_only_an_explicit_true_on_the_wire_enables(self) -> None:
        assert _grant(shellEnabled=True).shell_enabled is True

    def test_the_camel_case_alias_is_the_wire_spelling(self) -> None:
        # Electron emits `shellEnabled`; the snake_case name is the Python one.
        # Getting this backwards is how a field lands "green in every test and
        # dead on the real wire" — the same trap `_assert_host_session_wire_is_
        # private` documents for `grantId`.
        assert _grant(shellEnabled=True).shell_enabled is True
        assert "shellEnabled" in BrokerGrant.model_json_schema()["properties"]

    @pytest.mark.parametrize("bogus", ["true", "yes", "on", 1, {}, []])
    def test_a_non_boolean_flag_is_refused_rather_than_coerced(
        self, bogus: object
    ) -> None:
        """A malformed value fails closed — it never becomes ``True``.

        THIS TEST FOUND A REAL FAIL-OPEN. ``_BrokerModel`` is lax, and Pydantic's
        lax bool coercion accepts ``"true"``, ``"yes"``, ``"on"`` and ``1``, so
        a wire value of ``"yes"`` decoded to command authority on this machine.
        The field carries ``strict=True`` for exactly this, and a refusal here is
        a ``ValidationError`` — which the snapshot read already treats as a
        protocol failure, i.e. no grants at all, i.e. no command capability.
        """

        with pytest.raises(ValidationError):
            BrokerGrant.model_validate(
                {
                    "grantId": "grant_1",
                    "mode": "read_write",
                    "mount": "mnt_1",
                    "shellEnabled": bogus,
                }
            )

    def test_a_snapshot_of_mixed_grants_keeps_them_apart(self) -> None:
        snapshot = BrokerGrantSnapshot.model_validate(
            {
                "snapshotId": "snap_1",
                "capturedAt": 1.0,
                "grants": [
                    {
                        "grantId": "on",
                        "mode": "read_write",
                        "mount": "m1",
                        "root": "/a",
                        "shellEnabled": True,
                    },
                    {
                        "grantId": "off",
                        "mode": "read_write",
                        "mount": "m2",
                        "root": "/b",
                    },
                ],
            }
        )
        assert [g.shell_enabled for g in snapshot.grants] == [True, False]


class TestMountTableCarriesThePerWorkspaceDecision:
    """Per-workspace means per-workspace: enabling one enables exactly one."""

    def test_the_flag_does_not_leak_between_grants(self) -> None:
        mounts = WorkspaceMountTable.from_broker_grants(
            [
                _grant(
                    grantId="g_on",
                    label="enabled-project",
                    root="/Users/ada/code/enabled",
                    mount="m_on",
                    shellEnabled=True,
                ),
                _grant(
                    grantId="g_off",
                    label="other-project",
                    root="/Users/ada/code/other",
                    mount="m_off",
                ),
            ]
        )
        by_grant = {mount.grant_id: mount.shell_enabled for mount in mounts}
        assert by_grant == {"g_on": True, "g_off": False}

    def test_a_mount_built_without_the_field_is_off(self) -> None:
        # A caller that predates the flag (a test fixture, an older code path)
        # cannot accidentally produce a command-capable mount.
        assert WorkspaceMount(name="m", grant_id="g").shell_enabled is False

    def test_a_grant_whose_root_is_unusable_keeps_its_decision(self) -> None:
        """The degraded branch drops the ROOT, never the user's decision.

        A grant with a root this process cannot resolve still binds a mount (so
        broker-served reads keep working) but carries no host root. Dropping the
        shell flag there too would silently disable a workspace the user
        enabled — and it would do so only on the platform where the root
        happened not to normalise, which is the worst possible place to hide it.
        """

        mounts = WorkspaceMountTable.from_broker_grants(
            [_grant(root="not-an-absolute-path", shellEnabled=True)]
        )
        assert len(mounts) == 1
        assert mounts[0].host_root is None
        assert mounts[0].shell_enabled is True

    def test_a_revoked_grant_is_skipped_entirely(self) -> None:
        # Belt and braces with `toBrokerGrant`, which already omits the flag for
        # a non-active grant: even if a broker sent one, the table drops the
        # grant before the flag can matter.
        mounts = WorkspaceMountTable.from_broker_grants(
            [_grant(status="revoked", shellEnabled=True)]
        )
        assert mounts == ()


class TestGrantedRootIsThePrerequisiteTheFactoryReads:
    """§7.1's four prerequisites must stay four, not three."""

    def test_default_is_off(self) -> None:
        assert GrantedRoot(path="/Users/ada/code").shell_enabled is False
        assert GrantedRoot.from_host_path("/Users/ada/code").shell_enabled is False

    def test_writable_and_shell_enabled_are_independent(self) -> None:
        """A writable root is NOT a command-capable root, and vice versa.

        Prerequisites 2 and 3 are separate sentences in §7.1 and the factory
        checks both. Folding them together — "writable implies may run", or
        "may run implies writable" — would make one of the four prerequisites
        unobservable, which is exactly the failure mode where a security gate
        looks enforced because a DIFFERENT gate happens to be closed.
        """

        writable_only = GrantedRoot(path="/a", writable=True)
        assert writable_only.writable is True
        assert writable_only.shell_enabled is False

        shell_on_read_only = GrantedRoot(path="/b", writable=False, shell_enabled=True)
        assert shell_on_read_only.writable is False
        assert shell_on_read_only.shell_enabled is True

    def test_the_whole_read_path_end_to_end(self) -> None:
        """Wire → grant → mount → granted root, with one enabled among two."""

        snapshot = BrokerGrantSnapshot.model_validate(
            {
                "snapshotId": "snap_1",
                "capturedAt": 1.0,
                "grants": [
                    {
                        "grantId": "g_on",
                        "mode": "read_write",
                        "label": "enabled",
                        "mount": "m_on",
                        "root": "/Users/ada/code/enabled",
                        "shellEnabled": True,
                    },
                    {
                        "grantId": "g_off",
                        "mode": "read_write",
                        "label": "plain",
                        "mount": "m_off",
                        "root": "/Users/ada/code/plain",
                    },
                ],
            }
        )
        mounts = WorkspaceMountTable.from_broker_grants(snapshot.grants)
        roots = WorkspaceMountTable.granted_roots(mounts)
        by_path = {
            root.path: root.shell_enabled
            for root in roots
            if isinstance(root, GrantedRoot)
        }
        assert by_path == {
            "/Users/ada/code/enabled": True,
            "/Users/ada/code/plain": False,
        }

    def test_an_older_broker_that_sends_nothing_yields_no_command_capability(
        self,
    ) -> None:
        """Version skew degrades to "cannot run commands", never to "can"."""

        snapshot = BrokerGrantSnapshot.model_validate(
            {
                "snapshotId": "snap_1",
                "capturedAt": 1.0,
                # An older Electron main: no `shellEnabled` key anywhere.
                "grants": [
                    {
                        "grantId": "g1",
                        "mode": "read_write",
                        "label": "legacy",
                        "mount": "m1",
                        "root": "/Users/ada/legacy",
                    }
                ],
            }
        )
        roots = WorkspaceMountTable.granted_roots(
            WorkspaceMountTable.from_broker_grants(snapshot.grants)
        )
        assert [
            root.shell_enabled for root in roots if isinstance(root, GrantedRoot)
        ] == [False]

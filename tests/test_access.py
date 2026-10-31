"""Roles come from the OIDC provider's groups."""

from __future__ import annotations

import pytest

from pydebuprep_portal.access import Viewer, roles_from_groups


@pytest.mark.parametrize(
    ("groups", "roles"),
    [
        (["pydebuprep-admins"], {"admin"}),
        (["/org/pydebuprep-auditors"], {"auditor"}),
        (["pydebuprep-admins", "pydebuprep-auditors"], {"admin", "auditor"}),
        (["pydebuprep-admins-x", "admins", "pydebuprep-team-web"], set()),
        ("pydebuprep-admins", set()),
        ([1, None], set()),
        (None, set()),
    ],
)
def test_roles_from_groups(groups: object, roles: set[str]) -> None:
    assert roles_from_groups(groups, "pydebuprep-") == roles


def test_custom_prefix() -> None:
    assert roles_from_groups(["pdp-admins", "pydebuprep-admins"], "pdp-") == {"admin"}


def test_viewer_properties() -> None:
    assert Viewer("s", "n", frozenset({"admin"})).is_admin
    assert Viewer("s", "n", frozenset({"admin"})).reads_all
    assert Viewer("s", "n", frozenset({"auditor"})).reads_all
    assert not Viewer("s", "n", frozenset({"auditor"})).is_admin
    assert not Viewer("s", "n", frozenset()).reads_all

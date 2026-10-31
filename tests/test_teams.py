"""Teams: created at sign-in from the provider's groups."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from conftest import login
from oidc_stub import OIDCStub
from pydebuprep_portal.models import Team


def test_sign_in_creates_teams_and_stores_memberships(
    oidc_client: TestClient, stub: OIDCStub, db: Session
) -> None:
    login(oidc_client, stub, groups=["pydebuprep-team-web-lead", "pydebuprep-team-db"])
    teams = {t.name: t for t in db.query(Team)}
    assert set(teams) == {"web", "db"}
    assert teams["web"].public is False
    assert teams["web"].created_by == "sign-in"
    page = oidc_client.get("/").text
    assert '<span class="team">web (lead)</span>' in page
    assert '<span class="team">db</span>' in page


def test_sign_in_again_doesnt_duplicate_teams(
    oidc_client: TestClient, stub: OIDCStub, db: Session
) -> None:
    login(oidc_client, stub, groups=["pydebuprep-team-web"])
    login(oidc_client, stub, groups=["pydebuprep-team-web"])
    assert db.query(Team).count() == 1


def test_api_bearer_carries_teams_but_creates_none(
    oidc_client: TestClient, stub: OIDCStub, db: Session
) -> None:
    token = stub.access_token(groups=["pydebuprep-team-web", "pydebuprep-team-db-lead"])
    data = oidc_client.get("/api/v1/whoami", headers={"Authorization": f"Bearer {token}"}).json()
    assert (data["teams"], data["leads"]) == (["db", "web"], ["db"])
    assert db.query(Team).count() == 0

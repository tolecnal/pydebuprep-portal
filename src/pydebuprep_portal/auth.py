"""Signing in with OpenID Connect, and who is asking: a session for pages, a bearer token
for the API. Roles are read from the provider's groups at every sign-in."""

from __future__ import annotations

import json
import time
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.starlette_client import OAuth, StarletteOAuth2App
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet
from starlette.concurrency import run_in_threadpool

from pydebuprep_portal.access import Viewer, roles_from_groups, teams_from_groups
from pydebuprep_portal.security import require_csrf
from pydebuprep_portal.settings import Settings
from pydebuprep_portal.teams import ensure_teams
from pydebuprep_portal.templating import error_page

SESSION_USER = "user"
# The signed-in user's part of the session cookie; the whole cookie must stay under 4 KB.
MAX_SESSION_USER = 3000
# A token signed with a key id the provider hasn't published makes us fetch its keys
# again, but no more often than this: forged tokens must not hammer the provider.
KEY_REFETCH_SECONDS = 60.0
LEEWAY_SECONDS = 60
ALGORITHMS = ["RS256", "RS384", "RS512", "PS256", "ES256", "ES384", "EdDSA"]

router = APIRouter(include_in_schema=False)


class SignInRequired(Exception):
    def __init__(self, next_path: str) -> None:
        super().__init__(next_path)
        self.next_path = next_path


def make_oauth(settings: Settings) -> OAuth:
    oauth = OAuth()
    oauth.register(
        name="oidc",
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        server_metadata_url=f"{settings.oidc_issuer}/.well-known/openid-configuration",
        client_kwargs={"scope": "openid profile email"},
    )
    return oauth


def _client(request: Request) -> StarletteOAuth2App:
    client: StarletteOAuth2App = request.app.state.oauth.oidc
    return client


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def safe_next(value: object) -> str:
    """A local path to return to after signing in; anything else becomes /."""
    if not isinstance(value, str):
        return "/"
    local = value.startswith("/") and not value.startswith("//") and "\\" not in value
    return value if local else "/"


def display_name(claims: dict[str, Any]) -> str:
    for key in ("name", "preferred_username"):
        if isinstance(claims.get(key), str) and claims[key]:
            return str(claims[key])
    return str(claims["sub"])


def _viewer(claims: dict[str, Any], settings: Settings) -> Viewer:
    groups = claims.get(settings.oidc_groups_claim)
    teams, leads = teams_from_groups(groups, settings.group_prefix)
    return Viewer(
        subject=str(claims["sub"]),
        name=display_name(claims),
        roles=roles_from_groups(groups, settings.group_prefix),
        teams=teams,
        leads=leads,
    )


PROVIDER_DOWN = "the sign-in provider can't be reached"


@router.get("/login")
async def login(request: Request, next: str | None = None) -> Response:
    request.session["next"] = safe_next(next)
    redirect_uri = f"{_settings(request).base_url}/auth/callback"
    try:
        response: Response = await _client(request).authorize_redirect(request, redirect_uri)
    except httpx.HTTPError:
        raise HTTPException(503, PROVIDER_DOWN) from None
    return response


@router.get("/auth/callback")
async def callback(request: Request) -> Response:
    try:
        token = await _client(request).authorize_access_token(request)  # type: ignore[no-untyped-call]
        claims = dict(token["userinfo"])
    except (OAuthError, JoseError, httpx.HTTPError, KeyError, TypeError, ValueError):
        return error_page(
            request, 400, "Sign-in failed", "The sign-in could not be completed. Try again."
        )
    next_path = safe_next(request.session.get("next"))
    viewer = _viewer(claims, _settings(request))
    user = {
        "sub": viewer.subject,
        "name": viewer.name,
        "roles": sorted(viewer.roles),
        "teams": sorted(viewer.teams),
        "leads": sorted(viewer.leads),
    }
    if len(json.dumps(user)) > MAX_SESSION_USER:
        # A browser drops a cookie over 4 KB, and sign-in would go round in circles.
        return error_page(
            request, 400, "Sign-in failed",
            f"You are in too many teams ({len(viewer.teams)}) for the portal's session. "
            "Ask your identity provider's admins to trim your pydebuprep groups.",
        )  # fmt: skip
    await run_in_threadpool(_ensure_teams, request, viewer.teams)
    request.session.clear()  # a new session for the signed-in user
    request.session[SESSION_USER] = user
    return RedirectResponse(next_path, status_code=303)


def _ensure_teams(request: Request, names: frozenset[str]) -> None:
    """A team appears the first time someone signs in with its group."""
    with request.app.state.sessionmaker() as session:
        ensure_teams(session, names)


@router.post("/logout", dependencies=[Depends(require_csrf)])
async def logout(request: Request) -> Response:
    request.session.clear()
    settings = _settings(request)
    try:
        metadata = await _client(request).load_server_metadata()
    except httpx.HTTPError:
        metadata = {}
    end_session = metadata.get("end_session_endpoint")
    if not isinstance(end_session, str):
        return RedirectResponse("/", status_code=303)
    query = urlencode(
        {"client_id": settings.oidc_client_id, "post_logout_redirect_uri": f"{settings.base_url}/"}
    )
    return RedirectResponse(f"{end_session}?{query}", status_code=303)


def page_viewer(request: Request) -> Viewer:
    """Dependency for pages: the signed-in user, or a redirect to sign in."""
    user = request.session.get(SESSION_USER)
    if not isinstance(user, dict):
        path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise SignInRequired(path)
    return Viewer(
        str(user["sub"]),
        str(user["name"]),
        frozenset(user["roles"]),
        teams=frozenset(user.get("teams", [])),
        leads=frozenset(user.get("leads", [])),
    )


def sign_in_redirect(_: Request, exc: Exception) -> Response:
    """Exception handler for SignInRequired."""
    next_path = exc.next_path if isinstance(exc, SignInRequired) else "/"
    return RedirectResponse(f"/login?{urlencode({'next': next_path})}", status_code=303)


async def _keys(request: Request, refetch: bool) -> KeySet:
    client = _client(request)
    jwks = await client.fetch_jwk_set(force=refetch)
    return KeySet.import_key_set(jwks)


async def api_viewer(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> Viewer:
    """Dependency for the API: an OIDC access token (a JWT) from the provider."""
    refused = HTTPException(
        401, "expected an OIDC access token", headers={"WWW-Authenticate": "Bearer"}
    )
    scheme, _, raw = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not raw.strip() or raw.startswith("pdp_"):
        raise refused  # upload tokens never read anything
    settings = _settings(request)
    try:
        await _client(request).load_server_metadata()
        try:
            token = jwt.decode(raw.strip(), await _keys(request, False), algorithms=ALGORITHMS)
        except JoseError as exc:
            if "kid" not in str(exc).lower() or not _may_refetch(request):
                raise
            token = jwt.decode(raw.strip(), await _keys(request, True), algorithms=ALGORITHMS)
        jwt.JWTClaimsRegistry(
            leeway=LEEWAY_SECONDS,
            iss={"essential": True, "value": settings.oidc_issuer},
            aud={"essential": True, "value": settings.oidc_audience},
            exp={"essential": True},
            sub={"essential": True},
        ).validate(token.claims)
        if token.claims.get("typ") == "ID":  # an ID token is for the portal's sign-in only
            raise refused
    except httpx.HTTPError:
        raise HTTPException(503, PROVIDER_DOWN) from None
    except (JoseError, ValueError):
        raise refused from None
    return _viewer(token.claims, settings)


def _may_refetch(request: Request) -> bool:
    now = time.monotonic()
    last: float | None = getattr(request.app.state, "keys_refetched_at", None)
    if last is not None and now - last < KEY_REFETCH_SECONDS:
        return False
    request.app.state.keys_refetched_at = now
    return True

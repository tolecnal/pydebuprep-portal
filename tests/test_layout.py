"""The page layout: a sidebar with the navigation, the current page marked."""

from __future__ import annotations

import re
from collections.abc import Callable

from fastapi.testclient import TestClient

from conftest import csrf_of


def _current(page: str) -> list[str]:
    return re.findall(r'<a href="([^"]*)"[^>]*aria-current="page"', page)


def test_navigation_is_a_sidebar(admin: TestClient) -> None:
    page = admin.get("/").text
    sidebar = page.split('<aside class="sidebar"', 1)[1].split("</aside>", 1)[0]
    for href in ("/", "/hosts", "/search", "/teams", "/configs", "/queue", "/tokens",
                 "/settings", "/audit", "/docs"):  # fmt: skip
        assert f'href="{href}"' in sidebar
    assert 'action="/logout"' in sidebar


def test_sidebar_marks_the_current_page(admin: TestClient) -> None:
    assert _current(admin.get("/").text) == ["/"]
    assert _current(admin.get("/hosts").text) == ["/hosts"]
    assert _current(admin.get("/configs").text) == ["/configs"]


def test_a_page_below_a_section_marks_the_section(admin: TestClient) -> None:
    admin.post("/teams", data={"csrf_token": csrf_of(admin), "name": "web"})
    assert _current(admin.get("/teams/web").text) == ["/teams"]


def test_member_sidebar_leaves_out_admin_pages(signed_in: Callable[..., TestClient]) -> None:
    page = signed_in("pydebuprep-team-web").get("/").text
    assert 'href="/hosts"' in page
    assert 'href="/tokens"' not in page
    assert 'href="/audit"' not in page


def test_signed_out_pages_have_no_sidebar(client: TestClient) -> None:
    page = client.get("/hosts").text
    assert 'class="sidebar"' not in page

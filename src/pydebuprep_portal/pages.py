"""The portal's pages."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from pydebuprep_portal.access import Viewer
from pydebuprep_portal.auth import page_viewer
from pydebuprep_portal.templating import page

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def home(request: Request, viewer: Annotated[Viewer, Depends(page_viewer)]) -> HTMLResponse:
    return page(request, "home.html", viewer)

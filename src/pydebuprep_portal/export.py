"""Lists as CSV, for spreadsheets."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence

from fastapi.responses import Response

# Spreadsheets run a cell that starts with one of these as a formula; host names,
# finding titles and evidence come from the hosts, so none may.
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
# More than any page shows; a CSV is for taking everything that matches elsewhere.
MAX_ROWS = 10_000


def csv_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(FORMULA_START) else text


def csv_response(name: str, header: Sequence[str], rows: Iterable[Sequence[object]]) -> Response:
    """NAME.csv to download: HEADER, then ROWS, every cell safe to open."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    writer.writerows([csv_cell(v) for v in row] for row in rows)
    return Response(
        out.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
    )

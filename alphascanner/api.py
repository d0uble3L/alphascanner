import math
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, BeforeValidator, Field

from .db import connect, init_db
from .scanner import FilterParams, scan

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_SortBy = Literal["volume_surge", "pct_change_1h", "pct_change_24h", "pct_change_7d", "volume", "market_cap"]

_NoneIfEmpty = BeforeValidator(lambda v: None if v == "" else v)
# ge/le constraints live inside the float branch so None bypasses them
_OptFloat = Annotated[float | None, _NoneIfEmpty]
_OptFloatPos = Annotated[Annotated[float, Field(ge=0)] | None, _NoneIfEmpty]
_OptAthPct = Annotated[Annotated[float, Field(ge=0.0, le=1.0)] | None, _NoneIfEmpty]


class ScreenQuery(BaseModel):
    sort_by: _SortBy = "volume_surge"
    limit: Annotated[int, Field(ge=1, le=200)] = 20
    min_market_cap: _OptFloatPos = None
    max_market_cap: _OptFloatPos = None
    min_volume: _OptFloatPos = None
    min_pct_change_1h: _OptFloat = None
    min_pct_change_24h: _OptFloat = None
    min_pct_change_7d: _OptFloat = None
    min_volume_surge: _OptFloatPos = None
    near_ath_pct: _OptAthPct = None


def _format_number(v, places: int = 2) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    try:
        if abs(v) >= 1_000_000:
            return f"{v:,.0f}"
        if abs(v) >= 1:
            return f"{v:,.{places}f}"
        return f"{v:,.6f}"
    except (TypeError, ValueError):
        return str(v)


def _clean(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def _rows_for_display(df) -> list[dict]:
    if df.empty:
        return []
    out = []
    for r in df.to_dict(orient="records"):
        cleaned = {k: _clean(v) for k, v in r.items()}
        cleaned["price_fmt"] = _format_number(cleaned.get("current_price"), 4)
        cleaned["volume_fmt"] = _format_number(cleaned.get("total_volume"))
        cleaned["mcap_fmt"] = _format_number(cleaned.get("market_cap"))
        cleaned["surge_fmt"] = _format_number(cleaned.get("volume_surge"), 2)
        cleaned["chg1h_fmt"] = _format_number(cleaned.get("price_change_pct_1h"))
        cleaned["chg24h_fmt"] = _format_number(cleaned.get("price_change_pct_24h"))
        cleaned["chg7d_fmt"] = _format_number(cleaned.get("price_change_pct_7d"))
        cleaned["ath_fmt"] = _format_number(cleaned.get("ath_change_pct"))
        out.append(cleaned)
    return out


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="AlphaScanner", lifespan=lifespan)


def _latest_fetch_status() -> dict | None:
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT attempted_at, ok, status_code, message "
                "FROM fetch_log ORDER BY attempted_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def _build_params(q: ScreenQuery) -> FilterParams:
    return FilterParams(
        sort_by=q.sort_by,
        limit=q.limit,
        min_market_cap=q.min_market_cap,
        max_market_cap=q.max_market_cap,
        min_volume=q.min_volume,
        min_pct_change_1h=q.min_pct_change_1h,
        min_pct_change_24h=q.min_pct_change_24h,
        min_pct_change_7d=q.min_pct_change_7d,
        min_volume_surge=q.min_volume_surge,
        near_ath_pct=q.near_ath_pct,
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return TEMPLATES.TemplateResponse(request, "index.html")


@app.get("/screen", response_class=HTMLResponse)
async def screen_html(request: Request, q: Annotated[ScreenQuery, Depends()]):
    df, fetched_at = scan(_build_params(q))
    return TEMPLATES.TemplateResponse(
        request, "table.html",
        {
            "rows": _rows_for_display(df),
            "fetched_at": fetched_at,
            "fetch_status": _latest_fetch_status(),
        },
    )


@app.get("/api/screen")
async def screen_api(q: Annotated[ScreenQuery, Depends()]):
    df, fetched_at = scan(_build_params(q))
    rows = []
    if not df.empty:
        for r in df.to_dict(orient="records"):
            rows.append({k: _clean(v) for k, v in r.items()})
    return JSONResponse(
        {"fetched_at": fetched_at, "count": len(rows), "results": rows}
    )


@app.get("/healthz")
async def healthz():
    return {"ok": True}

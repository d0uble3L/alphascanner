import math
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from .db import init_db
from .scanner import FilterParams, scan

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


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


def _build_params(
    sort_by: str,
    limit: int,
    min_market_cap: float | None,
    max_market_cap: float | None,
    min_volume: float | None,
    min_pct_change_1h: float | None,
    min_pct_change_24h: float | None,
    min_pct_change_7d: float | None,
    min_volume_surge: float | None,
    near_ath_pct: float | None,
) -> FilterParams:
    return FilterParams(
        sort_by=sort_by,
        limit=limit,
        min_market_cap=min_market_cap,
        max_market_cap=max_market_cap,
        min_volume=min_volume,
        min_pct_change_1h=min_pct_change_1h,
        min_pct_change_24h=min_pct_change_24h,
        min_pct_change_7d=min_pct_change_7d,
        min_volume_surge=min_volume_surge,
        near_ath_pct=near_ath_pct,
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return TEMPLATES.TemplateResponse("index.html", {"request": request})


@app.get("/screen", response_class=HTMLResponse)
async def screen_html(
    request: Request,
    sort_by: str = "volume_surge",
    limit: int = 20,
    min_market_cap: float | None = None,
    max_market_cap: float | None = None,
    min_volume: float | None = None,
    min_pct_change_1h: float | None = None,
    min_pct_change_24h: float | None = None,
    min_pct_change_7d: float | None = None,
    min_volume_surge: float | None = None,
    near_ath_pct: float | None = None,
):
    params = _build_params(
        sort_by, limit, min_market_cap, max_market_cap, min_volume,
        min_pct_change_1h, min_pct_change_24h, min_pct_change_7d,
        min_volume_surge, near_ath_pct,
    )
    df, fetched_at = scan(params)
    return TEMPLATES.TemplateResponse(
        "table.html",
        {"request": request, "rows": _rows_for_display(df), "fetched_at": fetched_at},
    )


@app.get("/api/screen")
async def screen_api(
    sort_by: str = "volume_surge",
    limit: int = 20,
    min_market_cap: float | None = None,
    max_market_cap: float | None = None,
    min_volume: float | None = None,
    min_pct_change_1h: float | None = None,
    min_pct_change_24h: float | None = None,
    min_pct_change_7d: float | None = None,
    min_volume_surge: float | None = None,
    near_ath_pct: float | None = None,
):
    params = _build_params(
        sort_by, limit, min_market_cap, max_market_cap, min_volume,
        min_pct_change_1h, min_pct_change_24h, min_pct_change_7d,
        min_volume_surge, near_ath_pct,
    )
    df, fetched_at = scan(params)
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

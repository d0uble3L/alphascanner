import logging
import math
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from .config import settings
from .db import connect, init_db
from .presets import InvalidPresetName, delete_preset, list_presets, save_preset
from .scanner import ScreenQuery, scan

log = logging.getLogger(__name__)

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_basic_auth = HTTPBasic(auto_error=False)


def require_auth(credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic_auth)]) -> None:
    if not settings.auth_password:
        return  # auth disabled: no password configured
    valid = credentials is not None and (
        secrets.compare_digest(credentials.username, settings.auth_username)
        and secrets.compare_digest(credentials.password, settings.auth_password)
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


class _RateLimiter:
    """Fixed-window limiter, keyed by client IP. In-memory, single-process only."""

    def __init__(self, window_seconds: int = 60):
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window_seconds:
            hits.popleft()
        if len(hits) >= settings.rate_limit_per_minute:
            return False
        hits.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()


_rate_limiter = _RateLimiter()

_PresetParam = Annotated[
    str | None, Query(max_length=64, description="Run a saved preset instead of the filter params")
]


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


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    # /healthz stays unlimited: docker-compose polls it every 30s and it does
    # no DB/network work of its own.
    if request.url.path != "/healthz":
        client_ip = request.client.host if request.client else "unknown"
        if not _rate_limiter.allow(client_ip):
            return JSONResponse({"detail": "Too many requests"}, status_code=429)
    return await call_next(request)


def _latest_fetch_status() -> dict | None:
    try:
        with connect() as conn:
            row = conn.execute(
                "SELECT attempted_at, ok, status_code, message "
                "FROM fetch_log ORDER BY attempted_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        log.exception("Failed to read fetch status")
        return None


def _resolve_query(q: ScreenQuery, preset: str | None) -> ScreenQuery:
    """A named preset, when given, replaces the filter params entirely."""
    if preset is None:
        return q
    # Match the request value against stored names in Python instead of passing it
    # to SQL: the presets table is tiny, and this keeps untrusted input out of the
    # database layer entirely.
    saved = next((p for p in list_presets() if p.name == preset), None)
    if saved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preset not found")
    return saved.query


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def index(request: Request):
    return TEMPLATES.TemplateResponse(request, "index.html", {"presets": list_presets()})


@app.get("/screen", response_class=HTMLResponse, dependencies=[Depends(require_auth)])
async def screen_html(
    request: Request, q: Annotated[ScreenQuery, Depends()], preset: _PresetParam = None
):
    df, fetched_at = scan(_resolve_query(q, preset).to_filter_params())
    return TEMPLATES.TemplateResponse(
        request, "table.html",
        {
            "rows": _rows_for_display(df),
            "fetched_at": fetched_at,
            "fetch_status": _latest_fetch_status(),
        },
    )


@app.get("/api/screen", dependencies=[Depends(require_auth)])
async def screen_api(q: Annotated[ScreenQuery, Depends()], preset: _PresetParam = None):
    df, fetched_at = scan(_resolve_query(q, preset).to_filter_params())
    rows = []
    if not df.empty:
        for r in df.to_dict(orient="records"):
            rows.append({k: _clean(v) for k, v in r.items()})
    return JSONResponse(
        {"fetched_at": fetched_at, "count": len(rows), "results": rows}
    )


@app.get("/api/presets", dependencies=[Depends(require_auth)])
async def list_presets_api():
    return [
        {"name": p.name, "params": p.query.model_dump(), "updated_at": p.updated_at}
        for p in list_presets()
    ]


# Writes are PUT/DELETE only (never POST): HTML forms can't send them and they
# aren't CORS-safelisted, so another origin can't reuse browser-cached Basic Auth
# credentials to modify presets without a preflight this app never grants.
@app.put("/api/presets/{name}", dependencies=[Depends(require_auth)])
async def save_preset_api(name: str, query: ScreenQuery):
    try:
        save_preset(name, query)
    except InvalidPresetName as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return {"name": name, "params": query.model_dump()}


@app.delete("/api/presets/{name}", dependencies=[Depends(require_auth)])
async def delete_preset_api(name: str):
    if not delete_preset(name):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Preset not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.get("/healthz")
async def healthz():
    return {"ok": True}

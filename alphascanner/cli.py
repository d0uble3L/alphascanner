import asyncio
import math

import typer
from rich.console import Console
from rich.table import Table

from .db import init_db
from .fetcher import run_fetch
from .scanner import FilterParams, scan

app = typer.Typer(help="AlphaScanner — altcoin opportunity screener", no_args_is_help=True)
console = Console()


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    if isinstance(v, float):
        if abs(v) >= 1_000_000:
            return f"{v:,.0f}"
        if abs(v) >= 1:
            return f"{v:,.2f}"
        return f"{v:,.6f}"
    return str(v)


@app.command()
def init():
    """Initialize the SQLite database."""
    init_db()
    console.print("[green]Database initialized.[/green]")


@app.command()
def fetch():
    """Fetch a snapshot from CoinGecko and store it."""
    init_db()
    n, ts = asyncio.run(run_fetch())
    console.print(f"[green]Stored {n} coins[/green] at [cyan]{ts}[/cyan]")


@app.command()
def screen(
    sort_by: str = typer.Option(
        "volume_surge",
        help="volume_surge | pct_change_1h | pct_change_24h | pct_change_7d | volume | market_cap",
    ),
    limit: int = typer.Option(20, min=1, max=200),
    min_market_cap: float = typer.Option(None, help="Minimum market cap (USD)"),
    max_market_cap: float = typer.Option(None, help="Maximum market cap (USD)"),
    min_volume: float = typer.Option(None, help="Minimum 24h volume (USD)"),
    min_pct_change_1h: float = typer.Option(None),
    min_pct_change_24h: float = typer.Option(None),
    min_pct_change_7d: float = typer.Option(None),
    min_volume_surge: float = typer.Option(None, help="Ratio current_vol / avg_vol, e.g. 2.0"),
    near_ath_pct: float = typer.Option(
        None, help="0.95 means within 5%% of all-time high"
    ),
):
    """Show top movers under the given filters."""
    params = FilterParams(
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
    df, fetched_at = scan(params)
    if df.empty:
        console.print(
            "[yellow]No data yet — run `alphascanner fetch` first.[/yellow]"
        )
        raise typer.Exit(1)

    table = Table(title=f"Top {len(df)} by {sort_by} — snapshot {fetched_at}")
    cols = [
        ("symbol", "Symbol"),
        ("name", "Name"),
        ("current_price", "Price"),
        ("price_change_pct_1h", "1h %"),
        ("price_change_pct_24h", "24h %"),
        ("price_change_pct_7d", "7d %"),
        ("total_volume", "Volume"),
        ("volume_surge", "Vol surge"),
        ("market_cap", "Mkt cap"),
        ("ath_change_pct", "From ATH %"),
    ]
    for _, label in cols:
        table.add_column(label)
    for _, row in df.iterrows():
        table.add_row(*[_fmt(row[c]) for c, _ in cols])
    console.print(table)


if __name__ == "__main__":
    app()

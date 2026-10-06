import asyncio
import math
from dataclasses import asdict

import httpx
import typer
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .db import init_db
from .fetcher import run_fetch
from .presets import InvalidPresetName, delete_preset, get_preset, list_presets, save_preset
from .scanner import FilterParams, ScreenQuery, scan

app = typer.Typer(help="AlphaScanner — altcoin opportunity screener", no_args_is_help=True)
preset_app = typer.Typer(help="Manage saved screens (filter presets).", no_args_is_help=True)
app.add_typer(preset_app, name="preset")
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
    try:
        n, ts = asyncio.run(run_fetch())
    except httpx.HTTPStatusError as exc:
        console.print(f"[red]Fetch failed: HTTP {exc.response.status_code}[/red]")
        raise typer.Exit(1) from None
    except Exception as exc:  # noqa: BLE001 - surface any failure as a clean CLI message
        console.print(f"[red]Fetch failed: {exc}[/red]")
        raise typer.Exit(1) from None
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
    preset: str = typer.Option(
        None, help="Run a saved preset; the filter flags above are ignored"
    ),
    save_as: str = typer.Option(None, help="Save these filters as a named preset"),
):
    """Show top movers under the given filters."""
    init_db()
    if preset:
        saved = get_preset(preset)
        if saved is None:
            console.print(f"[red]No preset named {escape(repr(preset))}.[/red]")
            raise typer.Exit(1)
        params = saved.query.to_filter_params()
    else:
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

    if save_as:
        # Validate through the same model the API uses, so a preset saved here
        # can always be loaded by the web UI/API too.
        try:
            save_preset(save_as, ScreenQuery.model_validate(asdict(params)))
        except InvalidPresetName as exc:
            console.print(f"[red]{escape(str(exc))}[/red]")
            raise typer.Exit(1) from None
        except ValidationError as exc:
            console.print(f"[red]Invalid filters, preset not saved:[/red]\n{escape(str(exc))}")
            raise typer.Exit(1) from None
        console.print(f"[green]Saved preset {escape(repr(save_as))}.[/green]")

    df, fetched_at = scan(params)
    if df.empty:
        console.print(
            "[yellow]No data yet — run `alphascanner fetch` first.[/yellow]"
        )
        raise typer.Exit(1)

    title_source = f"preset {preset}" if preset else params.sort_by
    table = Table(title=f"Top {len(df)} by {escape(title_source)} — snapshot {fetched_at}")
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


@preset_app.command("list")
def preset_list():
    """List saved presets."""
    init_db()
    presets = list_presets()
    if not presets:
        console.print(
            "[yellow]No presets saved yet. Save one with "
            "`alphascanner screen ... --save-as NAME`.[/yellow]"
        )
        return
    table = Table(title="Saved presets")
    table.add_column("Name")
    table.add_column("Filters")
    table.add_column("Updated")
    for p in presets:
        filters = ", ".join(f"{k}={v}" for k, v in p.query.model_dump().items() if v is not None)
        table.add_row(p.name, escape(filters), p.updated_at)
    console.print(table)


@preset_app.command("delete")
def preset_delete(name: str):
    """Delete a saved preset."""
    init_db()
    if not delete_preset(name):
        console.print(f"[red]No preset named {escape(repr(name))}.[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Deleted preset {escape(repr(name))}.[/green]")


if __name__ == "__main__":
    app()

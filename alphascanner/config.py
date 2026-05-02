from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ALPHASCANNER_",
        extra="ignore",
    )

    coingecko_base_url: str = "https://api.coingecko.com/api/v3"
    coingecko_api_key: str | None = None
    db_path: str = "data/alphascanner.db"
    vs_currency: str = "usd"
    fetch_pages: int = 4
    per_page: int = 250
    fetch_interval_minutes: int = 15
    history_window: int = 20


settings = Settings()

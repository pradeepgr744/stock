from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Trading Assistant"
    app_version: str = "0.1.0"
    environment: str = "development"
    database_url: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/trading_assistant"
    upstox_access_token: str = ""
    upstox_api_base_url: str = "https://api.upstox.com"
    upstox_instruments_url: str = (
        "https://assets.upstox.com/market-quote/instruments/exchange/complete.json.gz"
    )
    upstox_request_timeout_seconds: float = 10.0
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    news_rss_feed_urls: str = ""
    orb_scanner_symbols: str = (
        "ADANIENT,ADANIPORTS,APOLLOHOSP,ASIANPAINT,AXISBANK,BAJAJ-AUTO,"
        "BAJFINANCE,BAJAJFINSV,BEL,BHARTIARTL,BPCL,BRITANNIA,CIPLA,COALINDIA,"
        "DRREDDY,EICHERMOT,GRASIM,HCLTECH,HDFCBANK,HDFCLIFE,HEROMOTOCO,"
        "HINDALCO,HINDUNILVR,ICICIBANK,INDUSINDBK,INFY,ITC,JIOFIN,JSWSTEEL,"
        "KOTAKBANK,LT,M&M,MARUTI,NESTLEIND,NTPC,ONGC,POWERGRID,RELIANCE,"
        "SBILIFE,SBIN,SHRIRAMFIN,SUNPHARMA,TATACONSUM,TMPV,TATASTEEL,"
        "TCS,TECHM,TITAN,ULTRACEMCO,WIPRO"
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

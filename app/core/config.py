"""All configuration comes from the .env file in the project root (or real environment variables)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _abs(p: str) -> str:
    q = Path(p)
    return str(q if q.is_absolute() else (PROJECT_ROOT / q).resolve())


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore")

    # --- engine
    ENGINE_ENV: str = "development"
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000
    ENABLE_DOCS: bool = True
    UI_ENABLED: bool = True
    LOG_DIR: str = "./logs"
    DATA_DIR: str = "./data"
    LOG_LEVEL: str = "INFO"

    # --- Oracle Autonomous Database (same variable names as your other project)
    ORACLE_ADB_USER: str = ""
    ORACLE_ADB_PASSWORD: str = ""
    ORACLE_ADB_DSN: str = ""           # TNS alias from wallet/tnsnames.ora, e.g. etldb_tp
    ORACLE_ADB_WALLET_DIR: str = "./wallet"
    ORACLE_ADB_WALLET_PASSWORD: str = ""
    DB_POOL_MAX: int = 5

    # --- OANDA
    OANDA_API_KEY: str = ""
    OANDA_ACCOUNT_ID: str = ""
    OANDA_API_URL: str = "https://api-fxpractice.oanda.com"
    OANDA_STREAM_URL: str = "https://stream-fxpractice.oanda.com"
    STREAM_STALL_SECONDS: int = 15
    STREAM_RECONNECT_MAX_BACKOFF: int = 60

    # --- pairs / history
    SEED_PAIRS: str = "EUR_USD,GBP_USD,USD_JPY,AUD_USD,USD_CAD,XAU_USD,EUR_GBP"
    BACKFILL_DAYS_NEW_PAIR: int = 7
    BACKFILL_ON_RECONNECT: bool = True

    # --- candle engine
    LIVE_TIMEFRAMES: str = "1m,5m,15m,30m,1h,4h"
    SESSION_MODE: str = "local"        # local = follows real Tokyo/London/New York clocks (moves 1 h in IST at clock changes); fixed = your IST times
    CANDLE_PUBLISH_INTERVAL_MS: int = 250
    CANDLE_CLOSE_GRACE_MS: int = 2000
    DB_FLUSH_INTERVAL_MS: int = 1000

    # --- storage
    STORE_TICKS: bool = False           # raw ticks fill the free 20 GB database quickly; keep off unless needed
    TICK_RETENTION_DAYS: int = 2
    CANDLE_1M_RETENTION_DAYS: int = 0   # 0 = keep forever

    # --- auth (generated automatically by:  python engine_ctl.py init)
    ADMIN_API_KEY: str = ""
    API_KEY_PEPPER: str = ""
    WS_TOKEN_SECRET: str = ""
    WS_TOKEN_TTL_SECONDS: int = 60
    WS_ALLOWED_ORIGINS: str = ""
    WS_MAX_CONNECTIONS: int = 5000
    WS_MAX_CONNECTIONS_PER_IP: int = 200
    WS_SEND_QUEUE_MAX: int = 500
    WS_AUTH_TIMEOUT_SECONDS: int = 5
    WS_MAX_SESSION_SECONDS: int = 86400
    WS_MAX_SUBSCRIPTIONS: int = 200

    # --- API behaviour
    MAX_CANDLES_PER_REQUEST: int = 5000
    MAX_TICKS_PER_REQUEST: int = 20000
    CACHE_TTL_LATEST_MS: int = 1000
    CACHE_TTL_HISTORY_S: int = 60
    CACHE_MAX_ENTRIES: int = 2000

    # --- alerts
    ALERT_WEBHOOK_URL: str = ""
    ALERT_COOLDOWN_SECONDS: int = 900
    STALE_PAIR_SECONDS: int = 120

    # ----------------------------------------------------------------- helpers
    @property
    def is_dev(self) -> bool:
        return self.ENGINE_ENV.lower() != "production"

    @property
    def wallet_dir(self) -> str:
        return _abs(self.ORACLE_ADB_WALLET_DIR)

    @property
    def log_dir(self) -> str:
        return _abs(self.LOG_DIR)

    @property
    def data_dir(self) -> str:
        return _abs(self.DATA_DIR)

    @property
    def seed_pairs(self) -> list[str]:
        return [s.strip().upper() for s in self.SEED_PAIRS.split(",") if s.strip()]

    @property
    def live_timeframes(self) -> list[str]:
        """Live timeframes plus the ones the analysis screen needs (1m 5m 15m 30m 1h), ordered by length."""
        from app.core.timeframes import TF_SECONDS
        tfs = {s.strip() for s in self.LIVE_TIMEFRAMES.split(",") if s.strip()} | {"1m", "5m", "15m", "30m", "1h"}
        return sorted(tfs, key=lambda x: TF_SECONDS[x])

    @property
    def ws_origins(self) -> list[str]:
        return [s.strip().rstrip("/") for s in self.WS_ALLOWED_ORIGINS.split(",") if s.strip()]

    def missing_for_start(self) -> list[str]:
        missing = [n for n in ("ORACLE_ADB_USER", "ORACLE_ADB_PASSWORD", "ORACLE_ADB_DSN", "ADMIN_API_KEY", "API_KEY_PEPPER", "WS_TOKEN_SECRET") if not getattr(self, n)]
        return missing

    def validate_secrets(self) -> None:
        if not self.is_dev:
            weak = [n for n in ("ADMIN_API_KEY", "API_KEY_PEPPER", "WS_TOKEN_SECRET") if len(getattr(self, n)) < 32]
            if weak:
                raise RuntimeError("Unsafe configuration: " + ", ".join(weak) + " must be at least 32 characters")


@lru_cache
def get_settings() -> Settings:
    return Settings()

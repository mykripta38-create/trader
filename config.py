"""Конфигурация бота: все настройки берутся из .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _require(name: str) -> str:
    """Обязательная переменная окружения."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"В файле .env не задана переменная {name}")
    return value


BOT_TOKEN: str = _require("BOT_TOKEN")
CHAT_ID: int = int(_require("CHAT_ID"))

# Список торговых пар через запятую в .env: SYMBOLS=BTC/USDT,ETH/USDT
SYMBOLS: list[str] = [
    s.strip()
    for s in os.getenv("SYMBOLS", os.getenv("SYMBOL", "BTC/USDT")).split(",")
    if s.strip()
]

TIMEFRAME: str = os.getenv("TIMEFRAME", "15m").strip()
CHECK_INTERVAL: int = int(os.getenv("CHECK_INTERVAL", "30"))
CANDLES_LIMIT: int = int(os.getenv("CANDLES_LIMIT", "500"))
SWING_PERIOD: int = int(os.getenv("SWING_PERIOD", "5"))
RISK_REWARD: float = float(os.getenv("RISK_REWARD", "2.0"))
TICK_SIZE: float = float(os.getenv("TICK_SIZE", "0.01"))
CHART_CANDLES: int = int(os.getenv("CHART_CANDLES", "70"))
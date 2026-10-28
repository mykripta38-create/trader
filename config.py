"""Конфигурация бота."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Переменная {name} не задана в окружении")
    return value

BOT_TOKEN: str = _require("BOT_TOKEN")
CHAT_ID: int = int(_require("CHAT_ID"))

BYBIT_API_KEY: str = os.getenv("BYBIT_API_KEY", "").strip()
BYBIT_API_SECRET: str = os.getenv("BYBIT_API_SECRET", "").strip()

SYMBOLS: list[str] = [
    s.strip()
    for s in os.getenv("SYMBOLS", "BTC/USDT").split(",")
    if s.strip()
]

TIMEFRAME: str = os.getenv("TIMEFRAME", "15m").strip()
CHECK_INTERVAL: int = int(os.getenv("CHECK_INTERVAL", "60"))
CANDLES_LIMIT: int = int(os.getenv("CANDLES_LIMIT", "300")) # 300, чтобы влезла EMA200
SWING_PERIOD: int = int(os.getenv("SWING_PERIOD", "5"))
RISK_REWARD: float = float(os.getenv("RISK_REWARD", "3.0"))
TICK_SIZE: float = float(os.getenv("TICK_SIZE", "0.01"))
CHART_CANDLES: int = int(os.getenv("CHART_CANDLES", "70"))

# Настройки трейлинг-стопа
TRAILING_TRIGGER_RR: float = float(os.getenv("TRAILING_TRIGGER_RR", "1.5"))

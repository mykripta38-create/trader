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

# 50 пар для сбора статистики
SYMBOLS: list[str] = [
    s.strip()
    for s in os.getenv("SYMBOLS", (
        "BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT,XRP/USDT,ADA/USDT,DOGE/USDT,"
        "AVAX/USDT,DOT/USDT,LINK/USDT,POL/USDT,LTC/USDT,TRX/USDT,ATOM/USDT,"
        "UNI/USDT,NEAR/USDT,APT/USDT,ARB/USDT,OP/USDT,SUI/USDT,SEI/USDT,"
        "GALA/USDT,ENS/USDT,CRV/USDT,WIF/USDT,BOME/USDT,TIA/USDT,INJ/USDT,"
        "FIL/USDT,ICP/USDT,HBAR/USDT,KAS/USDT,FET/USDT,JUP/USDT,PYTH/USDT,"
        "JTO/USDT,WLD/USDT,MANTA/USDT,DYM/USDT,PIXEL/USDT,PORTAL/USDT,"
        "ARKM/USDT,ENA/USDT,W/USDT,TON/USDT,FTM/USDT,SAND/USDT,MANA/USDT,"
        "GRT/USDT,CHZ/USDT"
    )).split(",")
    if s.strip()
]

TIMEFRAME: str = os.getenv("TIMEFRAME", "15m").strip()
CHECK_INTERVAL: int = int(os.getenv("CHECK_INTERVAL", "60"))  # Пауза между пачками
CANDLES_LIMIT: int = int(os.getenv("CANDLES_LIMIT", "200"))  # Минимум для EMA 200
SWING_PERIOD: int = int(os.getenv("SWING_PERIOD", "5"))
RISK_REWARD: float = float(os.getenv("RISK_REWARD", "3.0"))
TICK_SIZE: float = float(os.getenv("TICK_SIZE", "0.01"))
CHART_CANDLES: int = int(os.getenv("CHART_CANDLES", "70"))

# Трейлинг отключен
TRAILING_TRIGGER_RR: float = float(os.getenv("TRAILING_TRIGGER_RR", "10.0"))

# Риск-менеджмент
RISK_PERCENT: float = float(os.getenv("RISK_PERCENT", "1.0"))

# Размер пачки (сколько пар обрабатывать за один проход)
BATCH_SIZE: int = int(os.getenv("BATCH_SIZE", "10"))

"""Получение свечей с Binance Futures через ccxt (асинхронно)."""
import ccxt.async_support as ccxt
import pandas as pd
import time

import config


class BinanceData:
    def __init__(self) -> None:
        self.exchange = ccxt.binance({
            "enableRateLimit": True,
            "options": {
                "defaultType": "future",  # <-- Переключаемся на фьючерсы
            },
        })

    async def fetch_closed_candles(self, symbol: str, timeframe: str | None = None) -> pd.DataFrame:
        """Возвращает DataFrame закрытых фьючерсных свечей."""
        tf = timeframe or config.TIMEFRAME
        limit = config.CANDLES_LIMIT

        # Для фьючерсов Binance использует формат 'BTC/USDT:USDT'
        if ":" not in symbol:
            future_symbol = f"{symbol}:USDT"
        else:
            future_symbol = symbol

        tf_ms = self.exchange.parse_timeframe(tf) * 1000
        since = self.exchange.milliseconds() - (limit * tf_ms)

        raw = await self.exchange.fetch_ohlcv(
            future_symbol, tf, since=since, limit=limit
        )

        if not raw:
            raise ValueError(f"Binance вернул пустой ответ для {future_symbol} {tf}")

        df = pd.DataFrame(
            raw, columns=["ts", "Open", "High", "Low", "Close", "Volume"]
        )
        df["Date"] = pd.to_datetime(df["ts"], unit="ms")
        df = df.set_index("Date").drop(columns="ts").astype(float)
        
        # Последняя свеча еще формируется — отбрасываем её
        return df.iloc[:-1]

    async def close(self) -> None:
        await self.exchange.close()

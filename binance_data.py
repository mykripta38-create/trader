"""Получение свечей с Binance Spot через ccxt (асинхронно)."""
import ccxt.async_support as ccxt
import pandas as pd
import time

import config


class BinanceData:
    def __init__(self) -> None:
        # Возвращаемся на спотовый API (без defaultType: future)
        self.exchange = ccxt.binance({"enableRateLimit": True})

    async def fetch_closed_candles(self, symbol: str, timeframe: str | None = None) -> pd.DataFrame:
        """Возвращает DataFrame закрытых спотовых свечей."""
        tf = timeframe or config.TIMEFRAME
        limit = config.CANDLES_LIMIT

        # Для спота используем обычный формат 'BTC/USDT'
        tf_ms = self.exchange.parse_timeframe(tf) * 1000
        since = self.exchange.milliseconds() - (limit * tf_ms)

        raw = await self.exchange.fetch_ohlcv(
            symbol, tf, since=since, limit=limit
        )

        if not raw:
            raise ValueError(f"Binance вернул пустой ответ для {symbol} {tf}")

        df = pd.DataFrame(
            raw, columns=["ts", "Open", "High", "Low", "Close", "Volume"]
        )
        df["Date"] = pd.to_datetime(df["ts"], unit="ms")
        df = df.set_index("Date").drop(columns="ts").astype(float)
        
        # Последняя свеча еще формируется — отбрасываем её
        return df.iloc[:-1]

    async def close(self) -> None:
        await self.exchange.close()

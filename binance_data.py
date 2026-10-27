"""Получение свечей с Binance через ccxt (асинхронно)."""
import ccxt.async_support as ccxt
import pandas as pd
import config


class BinanceData:
    def __init__(self) -> None:
        self.exchange = ccxt.binance({"enableRateLimit": True})

    async def fetch_closed_candles(self, symbol: str, timeframe: str | None = None) -> pd.DataFrame:
        tf = timeframe or config.TIMEFRAME
        # Убрали since, чтобы избежать пустых ответов
        raw = await self.exchange.fetch_ohlcv(
            symbol, tf, limit=config.CANDLES_LIMIT
        )
        
        if not raw:
            raise ValueError(f"Binance вернул пустой ответ для {symbol} {tf}")

        df = pd.DataFrame(
            raw, columns=["ts", "Open", "High", "Low", "Close", "Volume"]
        )
        df["Date"] = pd.to_datetime(df["ts"], unit="ms")
        df = df.set_index("Date").drop(columns="ts").astype(float)
        return df.iloc[:-1]

    async def close(self) -> None:
        await self.exchange.close()

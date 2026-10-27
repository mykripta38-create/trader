"""Движок Smart Money Concepts: свинги, BOS/CHoCH, Order Block, ретест с фильтрами."""
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pandas_ta_classic as ta  # Требуется: pip install pandas_ta

OB_LOOKBACK = 50  # на сколько баров назад искать противоположную свечу


@dataclass
class OrderBlock:
    direction: str  # "long" (бычий OB) или "short" (медвежий OB)
    top: float
    bottom: float
    time: pd.Timestamp
    active: bool = True
    used: bool = False  # один OB — один сигнал

    @property
    def uid(self) -> str:
        return f"{self.direction}_{self.time:%Y%m%d%H%M}"


@dataclass
class Signal:
    uid: str
    direction: str
    entry: float
    sl: float
    tp: float
    rr: float
    ob_top: float
    ob_bottom: float
    ob_time: pd.Timestamp
    time: pd.Timestamp
    math_tp: float = 0.0
    logical_tp: float = 0.0


def _find_order_block(o, h, l, c, times, t: int, direction: str) -> OrderBlock | None:
    """Последняя противоположная свеча перед свечой пробоя t."""
    for i in range(t - 1, max(t - OB_LOOKBACK, 0) - 1, -1):
        bearish = c[i] < o[i]
        bullish = c[i] > o[i]
        if (direction == "long" and bearish) or (direction == "short" and bullish):
            return OrderBlock(direction, float(h[i]), float(l[i]), times[i])
    return None


def analyze(
    df: pd.DataFrame, 
    df_htf: pd.DataFrame, 
    swing_period: int = 5, 
    rr: float = 2.0, 
    tick: float = 0.01
) -> list[Signal]:
    """Проходит по свечам и возвращает сигналы с учетом фильтров."""
    half = max(1, swing_period // 2)
    o = df["Open"].to_numpy(float)
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    v = df["Volume"].to_numpy(float)
    times = df.index

    # --- ПОДГОТОВКА ФИЛЬТРОВ ---
    # 1. Тренд по старшему ТФ (4H, EMA 20)
    ema_htf = df_htf['Close'].ewm(span=20, adjust=False).mean().iloc[-1]
    htf_close = df_htf['Close'].iloc[-1]
    htf_trend = "long" if htf_close > ema_htf else "short"

    # 2. ATR для безопасного стоп-лосса
    atr_series = ta.atr(df['High'], df['Low'], df['Close'], length=14)
    # 3. Средний объем для фильтра всплеска
    avg_vol_series = df['Volume'].rolling(window=20).mean()

    last_sh: float | None = None
    last_sl: float | None = None
    trend = 0
    obs: list[OrderBlock] = []
    signals: list[Signal] = []

    for t in range(len(df)):
        # 1. Подтверждение свинга
        k = t - half
        if k >= half:
            if np.sum(h[k - half : t + 1] >= h[k]) == 1:
                last_sh = float(h[k])
            if np.sum(l[k - half : t + 1] <= l[k]) == 1:
                last_sl = float(l[k])

        # 2. Ретест активных OB с фильтрами
        for ob in obs:
            if not ob.active or ob.used:
                continue
            
            if ob.direction != htf_trend:
                continue

            if ob.direction == "long":
                if c[t] < ob.bottom:
                    ob.active = False
                    continue
                if trend == 1 and l[t] <= ob.top:
                    if pd.isna(avg_vol_series.iloc[t]) or v[t] < avg_vol_series.iloc[t] * 1.5:
                        continue

                    entry = float(c[t])
                    atr_val = atr_series.iloc[t] if not pd.isna(atr_series.iloc[t]) else (entry * 0.002)
                    sl = ob.bottom - (atr_val * 0.5)
                    
                    if entry > sl:
                        math_tp = entry + rr * (entry - sl)
                        logical_tp = last_sh + (last_sh * 0.001) if last_sh else math_tp
                        
                        # Выбираем более близкую цель
                        tp = logical_tp if (logical_tp < math_tp and logical_tp > entry) else math_tp
                        
                        # СТРОГИЙ ФИЛЬТР R:R (не менее 1:3)
                        current_rr = (tp - entry) / (entry - sl)
                        if current_rr < 3.0:  
                            continue
                            
                        signals.append(Signal(ob.uid, "long", entry, sl, tp, rr,
                                              ob.top, ob.bottom, ob.time, times[t],
                                              math_tp=math_tp, logical_tp=logical_tp))
                        ob.used = True
            else: # SHORT
                if c[t] > ob.top:
                    ob.active = False
                    continue
                if trend == -1 and h[t] >= ob.bottom:
                    if pd.isna(avg_vol_series.iloc[t]) or v[t] < avg_vol_series.iloc[t] * 1.5:
                        continue

                    entry = float(c[t])
                    atr_val = atr_series.iloc[t] if not pd.isna(atr_series.iloc[t]) else (entry * 0.002)
                    sl = ob.top + (atr_val * 0.5)
                    
                    if entry < sl:
                        math_tp = entry - rr * (sl - entry)
                        logical_tp = last_sl - (last_sl * 0.001) if last_sl else math_tp
                        
                        tp = logical_tp if (logical_tp > math_tp and logical_tp < entry) else math_tp
                        
                        # СТРОГИЙ ФИЛЬТР R:R (не менее 1:3)
                        current_rr = (entry - tp) / (sl - entry)
                        if current_rr < 3.0:  
                            continue
                            
                        signals.append(Signal(ob.uid, "short", entry, sl, tp, rr,
                                              ob.top, ob.bottom, ob.time, times[t],
                                              math_tp=math_tp, logical_tp=logical_tp))
                        ob.used = True

        # 3. BOS / CHoCH
        if last_sh is not None and c[t] > last_sh:
            ob = _find_order_block(o, h, l, c, times, t, "long")
            if ob:
                obs.append(ob)
            trend = 1
            last_sh = None
        elif last_sl is not None and c[t] < last_sl:
            ob = _find_order_block(o, h, l, c, times, t, "short")
            if ob:
                obs.append(ob)
            trend = -1
            last_sl = None

    return signals

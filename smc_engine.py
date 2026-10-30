"""Движок Smart Money Concepts: свинги, BOS/CHoCH, Order Block, ретест с фильтрами."""
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pandas_ta_classic as ta

OB_LOOKBACK = 50


@dataclass
class OrderBlock:
    direction: str
    top: float
    bottom: float
    time: pd.Timestamp
    active: bool = True
    used: bool = False

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
    timeframe: str = ""


def _find_order_block(o, h, l, c, times, t: int, direction: str) -> OrderBlock | None:
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
    rr: float = 3.0, 
    tick: float = 0.01
) -> list[dict]:
    if df.empty or df_htf.empty or len(df) < 2 or len(df_htf) < 200:
        return []
    if 'Close' not in df.columns or 'Close' not in df_htf.columns:
        return []

    half = max(1, swing_period // 2)
    o = df["Open"].to_numpy(float)
    h = df["High"].to_numpy(float)
    l = df["Low"].to_numpy(float)
    c = df["Close"].to_numpy(float)
    v = df["Volume"].to_numpy(float)
    times = df.index

    ema_fast = df_htf['Close'].ewm(span=50, adjust=False).mean().iloc[-1]
    ema_slow = df_htf['Close'].ewm(span=200, adjust=False).mean().iloc[-1]
    htf_close = df_htf['Close'].iloc[-1]
    
    if htf_close > ema_fast and htf_close > ema_slow and ema_fast > ema_slow:
        htf_trend = "long"
    elif htf_close < ema_fast and htf_close < ema_slow and ema_fast < ema_slow:
        htf_trend = "short"
    else:
        htf_trend = "none"

    atr_series = ta.atr(df['High'], df['Low'], df['Close'], length=14)
    avg_vol_series = df['Volume'].rolling(window=20).mean()

    last_sh: float | None = None
    last_sl: float | None = None
    trend = 0
    obs: list[OrderBlock] = []
    results: list[dict] = []

    for t in range(len(df)):
        k = t - half
        if k >= half:
            if np.sum(h[k - half : t + 1] >= h[k]) == 1:
                last_sh = float(h[k])
            if np.sum(l[k - half : t + 1] <= l[k]) == 1:
                last_sl = float(l[k])

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
                    vol_ok = not (pd.isna(avg_vol_series.iloc[t]) or v[t] < avg_vol_series.iloc[t] * 1.5)
                    
                    entry = float(c[t])
                    atr_val = atr_series.iloc[t] if not pd.isna(atr_series.iloc[t]) else (entry * 0.002)
                    sl = ob.bottom - (atr_val * 0.5)
                    
                    if entry > sl:
                        math_tp = entry + rr * (entry - sl)
                        logical_tp = last_sh + (last_sh * 0.001) if last_sh else math_tp
                        tp = logical_tp if (logical_tp < math_tp and logical_tp > entry) else math_tp
                        
                        current_rr = (tp - entry) / (entry - sl)
                        rr_ok = current_rr >= 3.0
                        
                        reason = []
                        if not vol_ok: reason.append(f"Объем ({v[t]/avg_vol_series.iloc[t]:.1f}x < 1.5x)")
                        if not rr_ok: reason.append(f"R:R ({current_rr:.2f} < 3.0)")
                        
                        sig = Signal(ob.uid, "long", entry, sl, tp, rr,
                                     ob.top, ob.bottom, ob.time, times[t],
                                     math_tp=math_tp, logical_tp=logical_tp)
                        results.append({"signal": sig, "is_valid": len(reason) == 0, "reason": ", ".join(reason)})
                        
                        if len(reason) == 0:
                            ob.used = True
            else:
                if c[t] > ob.top:
                    ob.active = False
                    continue
                if trend == -1 and h[t] >= ob.bottom:
                    vol_ok = not (pd.isna(avg_vol_series.iloc[t]) or v[t] < avg_vol_series.iloc[t] * 1.5)
                    
                    entry = float(c[t])
                    atr_val = atr_series.iloc[t] if not pd.isna(atr_series.iloc[t]) else (entry * 0.002)
                    sl = ob.top + (atr_val * 0.5)
                    
                    if entry < sl:
                        math_tp = entry - rr * (sl - entry)
                        logical_tp = last_sl - (last_sl * 0.001) if last_sl else math_tp
                        tp = logical_tp if (logical_tp > math_tp and logical_tp < entry) else math_tp
                        
                        current_rr = (entry - tp) / (sl - entry)
                        rr_ok = current_rr >= 3.0
                        
                        reason = []
                        if not vol_ok: reason.append(f"Объем ({v[t]/avg_vol_series.iloc[t]:.1f}x < 1.5x)")
                        if not rr_ok: reason.append(f"R:R ({current_rr:.2f} < 3.0)")
                        
                        sig = Signal(ob.uid, "short", entry, sl, tp, rr,
                                     ob.top, ob.bottom, ob.time, times[t],
                                     math_tp=math_tp, logical_tp=logical_tp)
                        results.append({"signal": sig, "is_valid": len(reason) == 0, "reason": ", ".join(reason)})
                        
                        if len(reason) == 0:
                            ob.used = True

        if last_sh is not None and c[t] > last_sh:
            ob = _find_order_block(o, h, l, c, times, t, "long")
            if ob: obs.append(ob)
            trend = 1
            last_sh = None
        elif last_sl is not None and c[t] < last_sl:
            ob = _find_order_block(o, h, l, c, times, t, "short")
            if ob: obs.append(ob)
            trend = -1
            last_sl = None

    return results

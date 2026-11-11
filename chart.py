"""Облегченный график сигнала на чистом matplotlib."""
import asyncio
import io
import gc
import matplotlib

# Используем Agg бэкенд (без GUI) для экономии памяти
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Rectangle

import config
from smc_engine import Signal

PAD = 5

def _build_light_chart(df: pd.DataFrame, sig: Signal, symbol: str) -> bytes:
    # Ограничиваем количество свечей для экономии памяти (20 вместо 30)
    view = df.loc[: sig.time].tail(20) 
    n = len(view)
    is_long = sig.direction == "long"

    # Уменьшенный размер и DPI
    fig, ax = plt.subplots(figsize=(5, 5), dpi=70) 
    
    try:
        for i, (idx, row) in enumerate(view.iterrows()):
            color = "#26a69a" if row['Close'] >= row['Open'] else "#ef5350"
            ax.plot([i, i], [row['Low'], row['High']], color=color, linewidth=1)
            rect = Rectangle((i - 0.3, min(row['Open'], row['Close'])), 0.6, abs(row['Close'] - row['Open']),
                             facecolor=color, edgecolor=color)
            ax.add_patch(rect)

        x0 = view.index.get_loc(sig.ob_time) if sig.ob_time in view.index else 0
        ax.add_patch(Rectangle((x0 - 0.5, sig.ob_bottom), n + PAD - x0, sig.ob_top - sig.ob_bottom,
                               facecolor="#2962ff", alpha=0.3, edgecolor="#2962ff", zorder=1))

        ax.axhline(sig.entry, color="#ffd54f", linestyle="--", linewidth=1)
        ax.axhline(sig.sl, color="#ef5350", linestyle="--", linewidth=1)
        ax.axhline(sig.tp, color="#26a69a", linestyle="--", linewidth=1)

        ax.set_title(f"{symbol} {'LONG' if is_long else 'SHORT'}", fontsize=9, color="white")
        ax.set_xlim(-1, n + PAD)
        
        lo = min(float(view["Low"].min()), sig.sl, sig.tp)
        hi = max(float(view["High"].max()), sig.sl, sig.tp)
        margin = (hi - lo) * 0.05
        ax.set_ylim(lo - margin, hi + margin)

        fig.patch.set_facecolor('#1e222d')
        ax.set_facecolor('#1e222d')
        ax.tick_params(colors='white', labelsize=7)
        for spine in ax.spines.values():
            spine.set_color('white')
        ax.grid(True, color='#363c4e', linestyle='--', linewidth=0.5)
        
        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        return buf.getvalue()
    finally:
        # КРИТИЧНО ДЛЯ ПАМЯТИ: закрываем ВСЕ фигуры и чистим мусор
        plt.close('all') 
        gc.collect()

async def render_chart(df: pd.DataFrame, sig: Signal, symbol: str) -> bytes:
    return await asyncio.to_thread(_build_light_chart, df, sig, symbol)

"""График сигнала: свечи, Order Block, вход, Stop-Loss, Take-Profit."""
import asyncio
import io

import matplotlib

matplotlib.use("Agg")  # без окон, только в файл
import matplotlib.pyplot as plt  # noqa: E402
import mplfinance as mpf  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

import config  # noqa: E402
from smc_engine import Signal  # noqa: E402

PAD = 14  # пустое место справа под подписи уровней


def _build_chart(df: pd.DataFrame, sig: Signal, symbol: str) -> bytes:
    view = df.loc[: sig.time].tail(config.CHART_CANDLES)
    n = len(view)
    is_long = sig.direction == "long"

    colors = mpf.make_marketcolors(up="#26a69a", down="#ef5350", edge="inherit", wick="inherit")
    style = mpf.make_mpf_style(base_mpf_style="nightclouds", marketcolors=colors)

    fig, axes = mpf.plot(
        view,
        type="candle",
        style=style,
        figsize=(8, 10),  # вертикальный формат — удобно на телефоне
        returnfig=True,
        datetime_format="%d.%m %H:%M",
        xrotation=0,
        title=f"\n{symbol} {config.TIMEFRAME} — {'LONG' if is_long else 'SHORT'}",
    )
    ax = axes[0]

    # Order Block — прямоугольник от свечи OB до правого края
    x0 = view.index.get_loc(sig.ob_time) if sig.ob_time in view.index else 0
    ax.add_patch(Rectangle((x0 - 0.5, sig.ob_bottom), n + PAD - x0, sig.ob_top - sig.ob_bottom,
                           facecolor="#2962ff", alpha=0.30, edgecolor="#2962ff", zorder=1))
    ax.text(x0, sig.ob_top, " Order Block", color="#82b1ff", fontsize=11,
            fontweight="bold", va="bottom")

    # Уровни входа, SL и TP
    levels = [
        (sig.entry, "#ffd54f", f"ENTRY {sig.entry:,.1f}"),
        (sig.sl, "#ef5350", f"SL {sig.sl:,.1f}"),
        (sig.tp, "#26a69a", f"TP {sig.tp:,.1f}"),
    ]
    for price, color, label in levels:
        ax.axhline(price, color=color, linestyle="--", linewidth=1.4, zorder=3)
        ax.text(n - 0.3, price, label, color=color, fontsize=11,
                fontweight="bold", va="bottom", ha="left")

    # Точка входа на свече ретеста
    ax.scatter([n - 1], [sig.entry], marker="^" if is_long else "v", s=180,
               color="#ffd54f", edgecolors="black", zorder=5)

    # Границы осей, чтобы SL и TP точно попали в кадр
    lo = min(float(view["Low"].min()), sig.sl, sig.tp)
    hi = max(float(view["High"].max()), sig.sl, sig.tp)
    margin = (hi - lo) * 0.05
    ax.set_xlim(-1, n + PAD)
    ax.set_ylim(lo - margin, hi + margin)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


async def render_chart(df: pd.DataFrame, sig: Signal, symbol: str) -> bytes:
    """Рисуем в отдельном потоке, чтобы не блокировать event loop."""
    return await asyncio.to_thread(_build_chart, df, sig, symbol)
"""Точка входа: python main.py"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import config
from binance_data import BinanceData
from bybit_trader import BybitTrader
from handlers import router, state
from smc_engine import Signal, analyze

SENT_FILE = config.BASE_DIR / "sent_signals.json"


def load_sent() -> set[str]:
    try:
        return set(json.loads(SENT_FILE.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_sent(sent: set[str]) -> None:
    SENT_FILE.write_text(json.dumps(sorted(sent)[-500:]), encoding="utf-8")


def format_price(price: float) -> str:
    if price == 0: return "0"
    if price < 0.001: return f"{price:,.6f}"
    if price < 1: return f"{price:,.5f}"
    if price < 100: return f"{price:,.3f}"
    return f"{price:,.2f}"


def format_signal(sig: Signal, symbol: str) -> str:
    is_long = sig.direction == "long"
    risk_pct = abs(sig.entry - sig.sl) / sig.entry * 100
    
    if is_long:
        actual_rr = (sig.tp - sig.entry) / (sig.entry - sig.sl)
    else:
        actual_rr = (sig.entry - sig.tp) / (sig.sl - sig.entry)

    return (
        f"{'🟢' if is_long else '🔴'} <b>{'LONG' if is_long else 'SHORT'} "
        f"{symbol}</b> ({sig.timeframe})\n\n"
        f"Вход: <b>{format_price(sig.entry)}</b>\n"
        f"Stop-Loss: <b>{format_price(sig.sl)}</b>\n"
        f"Take-Profit: <b>{format_price(sig.tp)}</b>\n"
        f"R:R = 1:{actual_rr:.2f} | риск {risk_pct:.2f}%\n\n"
        f"Order Block: {format_price(sig.ob_bottom)} – {format_price(sig.ob_top)}\n"
        f"Свеча ретеста (UTC): {sig.time:%Y-%m-%d %H:%M}"
    )


async def send_signal(bot: Bot, df, sig: Signal, symbol: str, trader: BybitTrader) -> None:
    text = format_signal(sig, symbol)
    
    try:
        await bot.send_message(config.CHAT_ID, text, parse_mode=ParseMode.HTML)
        state.last_signal_text = text
        state.signals_sent += 1
    except Exception as e:
        logging.error(f"❌ Ошибка при отправке ТЕКСТА: {e}")

    if config.BYBIT_API_KEY:
        await trader.create_trade(symbol, sig.direction, sig.entry, sig.sl, sig.tp)


last_signal_time = {}

async def signal_loop(bot: Bot, data: BinanceData, symbol: str, trader: BybitTrader) -> None:
    sent = load_sent()
    last_ts_15m = None
    
    while True:
        try:
            current_time = time.time()

            df_15m = await data.fetch_closed_candles(symbol, timeframe="15m")
            df_1h = await data.fetch_closed_candles(symbol, timeframe="1h")

            all_signals = []

            if last_ts_15m is None or df_15m.index[-1] > last_ts_15m:
                ema20 = df_15m['Close'].ewm(span=20, adjust=False).mean().iloc[-1]
                ema50 = df_15m['Close'].ewm(span=50, adjust=False).mean().iloc[-1]
                close_15m = df_15m['Close'].iloc[-1]
                trend_15m = "long" if (close_15m > ema20 and ema20 > ema50) else ("short" if (close_15m < ema20 and ema20 < ema50) else "none")

                if trend_15m != "none":
                    sigs_15m = await asyncio.to_thread(
                        analyze, df_15m, df_1h, config.SWING_PERIOD, config.RISK_REWARD, config.TICK_SIZE
                    )
                    for s in sigs_15m:
                        if s.direction == trend_15m:
                            s.timeframe = "15m"
                            all_signals.append((s, df_15m))
                last_ts_15m = df_15m.index[-1]

            for sig, df_source in all_signals:
                key = f"{symbol}_{sig.timeframe}_{sig.uid}"
                
                fresh = (sig.time == df_15m.index[-1] if last_ts_15m else sig.time > (last_ts_15m or 0))
                if not fresh or key in sent:
                    continue

                cooldown_key = f"{symbol}_{sig.timeframe}"
                last_time = last_signal_time.get(cooldown_key, 0)
                if current_time - last_time < 3600:
                    continue

                await send_signal(bot, df_source, sig, symbol, trader)
                
                last_signal_time[cooldown_key] = current_time
                sent.add(key)
                save_sent(sent)
                
            state.last_candle = f"{df_15m.index[-1]:%Y-%m-%d %H:%M}"
            
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception(f"Ошибка в цикле проверки ({symbol})")
        await asyncio.sleep(config.CHECK_INTERVAL)


async def daily_report(bot: Bot):
    while True:
        now = datetime.now(timezone.utc)
        seconds_until_midnight = (24 - now.hour - 1) * 3600 + (60 - now.minute - 1) * 60 + (60 - now.second)
        await asyncio.sleep(seconds_until_midnight)
        
        try:
            text = (
                f"📊 <b>Ежедневный отчет</b>\n"
                f"Дата: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n"
                f"Отправлено сигналов: {state.signals_sent}\n"
                f"Последняя свеча: {state.last_candle}\n"
            )
            await bot.send_message(config.CHAT_ID, text, parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.error(f"Ошибка отправки отчета: {e}")


async def start_health_server():
    async def health_check(request):
        return web.Response(text="OK", status=200)
    
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    
    port = int(os.getenv("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"🌐 Keep-Alive сервер запущен на порту {port}")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    
    await start_health_server()
    
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    data = BinanceData()
    trader = BybitTrader()

    tasks = [asyncio.create_task(signal_loop(bot, data, sym, trader)) for sym in config.SYMBOLS]
    tasks.append(asyncio.create_task(daily_report(bot)))

    try:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception as e:
            print(f"Не удалось удалить вебхук: {e}")

        try:
            await bot.send_message(
                config.CHAT_ID, f"Бот запущен (15m + R:R 1:3, 4 пары): {len(config.SYMBOLS)}"
            )
        except Exception:
            logging.exception("Ошибка стартового сообщения")

        await dp.start_polling(bot)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await data.close()
        await trader.close()
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
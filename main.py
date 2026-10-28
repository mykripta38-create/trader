"""Точка входа: python main.py"""
import asyncio
import json
import logging
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile

import config
from binance_data import BinanceData
from bybit_trader import BybitTrader
from chart import render_chart
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
    """Умное форматирование цены в зависимости от её размера."""
    if price == 0:
        return "0"
    if price < 0.001:
        return f"{price:,.6f}"
    if price < 1:
        return f"{price:,.5f}"  # Для ADA, XRP, DOGE
    if price < 100:
        return f"{price:,.3f}"  # Для SOL, UNI, LINK, LTC
    return f"{price:,.2f}"      # Для BTC, ETH


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
        f"<i>Цели:</i>\n"
        f"├ Логическая (свинг): {format_price(sig.logical_tp)}\n"
        f"└ Математическая (1:{sig.rr:g}): {format_price(sig.math_tp)}\n\n"
        f"Order Block: {format_price(sig.ob_bottom)} – {format_price(sig.ob_top)}\n"
        f"Свеча ретеста (UTC): {sig.time:%Y-%m-%d %H:%M}"
    )


async def send_signal(bot: Bot, df, sig: Signal, symbol: str, trader: BybitTrader) -> None:
    text = format_signal(sig, symbol)
    
    # 1. Отправляем текст
    try:
        await bot.send_message(config.CHAT_ID, text, parse_mode=ParseMode.HTML)
        state.last_signal_text = text
        state.signals_sent += 1
    except Exception as e:
        logging.error(f"❌ Ошибка при отправке ТЕКСТА: {e}")

    # 2. Отправляем график
    try:
        img = await render_chart(df, sig, symbol)
        await bot.send_photo(config.CHAT_ID, BufferedInputFile(img, filename="signal.png"))
    except Exception as e:
        logging.error(f"❌ Ошибка при отправке ГРАФИКА: {e}")

    # 3. Выставляем ордер на Bybit Demo
    if config.BYBIT_API_KEY:
        await trader.create_trade(symbol, sig.direction, sig.entry, sig.sl, sig.tp)


last_signal_time = {}

async def signal_loop(bot: Bot, data: BinanceData, symbol: str, trader: BybitTrader) -> None:
    """Фоновый цикл: проверяем 15m и 1h одновременно."""
    sent = load_sent()
    last_ts_15m = None
    last_ts_1h = None
    
    while True:
        try:
            # 1. Запрашиваем свечи для всех трех ТФ
            df_15m = await data.fetch_closed_candles(symbol, timeframe="15m")
            df_1h = await data.fetch_closed_candles(symbol, timeframe="1h")
            df_4h = await data.fetch_closed_candles(symbol, timeframe="4h")

            all_signals = []

            # 2. Анализ 15m (фильтр: 1h)
            if last_ts_15m is None or df_15m.index[-1] > last_ts_15m:
                sigs_15m = await asyncio.to_thread(
                    analyze, df_15m, df_1h, config.SWING_PERIOD, config.RISK_REWARD, config.TICK_SIZE
                )
                for s in sigs_15m:
                    s.timeframe = "15m"
                    all_signals.append((s, df_15m))
                last_ts_15m = df_15m.index[-1]

            # 3. Анализ 1h (фильтр: 4h)
            if last_ts_1h is None or df_1h.index[-1] > last_ts_1h:
                sigs_1h = await asyncio.to_thread(
                    analyze, df_1h, df_4h, config.SWING_PERIOD, config.RISK_REWARD, config.TICK_SIZE
                )
                for s in sigs_1h:
                    s.timeframe = "1h"
                    all_signals.append((s, df_1h))
                last_ts_1h = df_1h.index[-1]

            # 4. Фильтрация и отправка
            for sig, df_source in all_signals:
                key = f"{symbol}_{sig.timeframe}_{sig.uid}"
                
                # Проверка на свежесть
                fresh = False
                if sig.timeframe == "15m":
                    fresh = (sig.time == df_15m.index[-1] if last_ts_15m else sig.time > (last_ts_15m or 0))
                else:
                    fresh = (sig.time == df_1h.index[-1] if last_ts_1h else sig.time > (last_ts_1h or 0))

                if not fresh or key in sent:
                    continue

                # Cooldown 1 час (отдельно для 15m и 1h по каждой паре)
                cooldown_key = f"{symbol}_{sig.timeframe}"
                current_time = time.time()
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


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    data = BinanceData()
    trader = BybitTrader()

    tasks = [asyncio.create_task(signal_loop(bot, data, sym, trader)) for sym in config.SYMBOLS]

    try:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception as e:
            print(f"Не удалось удалить вебхук: {e}")

        try:
            await bot.send_message(
                config.CHAT_ID, f"Бот запущен (15m + 1h): {len(config.SYMBOLS)} пар"
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

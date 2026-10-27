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
        f"Вход: <b>{sig.entry:,.2f}</b>\n"
        f"Stop-Loss: <b>{sig.sl:,.2f}</b>\n"
        f"Take-Profit: <b>{sig.tp:,.2f}</b>\n"
        f"R:R = 1:{actual_rr:.2f} | риск {risk_pct:.2f}%\n\n"
        f"<i>Цели:</i>\n"
        f"├ Логическая (свинг): {sig.logical_tp:,.2f}\n"
        f"└ Математическая (1:{sig.rr:g}): {sig.math_tp:,.2f}\n\n"
        f"Order Block: {sig.ob_bottom:,.2f} – {sig.ob_top:,.2f}\n"
        f"Свеча ретеста (UTC): {sig.time:%Y-%m-%d %H:%M}"
    )


async def send_signal(bot: Bot, df, sig: Signal, symbol: str) -> None:
    text = format_signal(sig, symbol)
    await bot.send_message(config.CHAT_ID, text)
    state.last_signal_text = text
    state.signals_sent += 1
    try:
        img = await render_chart(df, sig, symbol)
        await bot.send_photo(
            config.CHAT_ID, BufferedInputFile(img, filename="signal.png")
        )
    except Exception:
        logging.exception(f"Не удалось построить или отправить график {symbol}")


last_signal_time = {}

async def signal_loop(bot: Bot, data: BinanceData, symbol: str) -> None:
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

                await send_signal(bot, df_source, sig, symbol)
                
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

    tasks = [asyncio.create_task(signal_loop(bot, data, sym)) for sym in config.SYMBOLS]

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
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

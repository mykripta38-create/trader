"""Точка входа: python main.py"""
import asyncio
import json
import logging
import time  # <-- Добавлено для Cooldown

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
    """Загружаем ID уже отправленных сигналов (защита от дублей после перезапуска)."""
    try:
        return set(json.loads(SENT_FILE.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def save_sent(sent: set[str]) -> None:
    SENT_FILE.write_text(json.dumps(sorted(sent)[-500:]), encoding="utf-8")


def format_signal(sig: Signal, symbol: str) -> str:
    is_long = sig.direction == "long"
    risk_pct = abs(sig.entry - sig.sl) / sig.entry * 100
    return (
        f"{'🟢' if is_long else '🔴'} <b>{'LONG' if is_long else 'SHORT'} "
        f"{symbol}</b> ({config.TIMEFRAME})\n\n"
        f"Вход: <b>{sig.entry:,.2f}</b>\n"
        f"Stop-Loss: <b>{sig.sl:,.2f}</b>\n"
        f"Take-Profit: <b>{sig.tp:,.2f}</b>\n"
        f"R:R = 1:{sig.rr:g} | риск {risk_pct:.2f}%\n\n"
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


# Словарь для защиты от спама сигналами по одной паре (Cooldown)
last_signal_time = {}

async def signal_loop(bot: Bot, data: BinanceData, symbol: str) -> None:
    """Фоновый цикл: проверяем новые закрытые свечи и ищем сигналы."""
    sent = load_sent()
    last_ts = None  # время последней обработанной свечи
    while True:
        try:
            # 1. Запрашиваем свечи младшего ТФ (15m) для поиска OB
            df = await data.fetch_closed_candles(symbol)
            # 2. Запрашиваем свечи старшего ТФ (1h) для фильтра тренда
            df_htf = await data.fetch_closed_candles(symbol, timeframe="1h")

            if last_ts is None or df.index[-1] > last_ts:
                # Передаем df_htf в обновленный analyze
                signals = await asyncio.to_thread(
                    analyze, df, df_htf, config.SWING_PERIOD, config.RISK_REWARD, config.TICK_SIZE
                )
                for sig in signals:
                    # Уникальный ключ с учётом символа
                    key = f"{symbol}_{sig.uid}"
                    # Первый запуск — только сигнал на последней свече
                    fresh = (
                        sig.time == df.index[-1] if last_ts is None else sig.time > last_ts
                    )
                    if not fresh or key in sent:
                        continue

                    # --- ФИЛЬТР COOLDOWN (1 час) ---
                    current_time = time.time()
                    last_time = last_signal_time.get(symbol, 0)
                    if current_time - last_time < 3600:
                        logging.info(f"Сигнал {symbol} пропущен (Cooldown 1 час).")
                        continue

                    await send_signal(bot, df, sig, symbol)
                    
                    # Записываем время успешной отправки
                    last_signal_time[symbol] = current_time
                    sent.add(key)
                    save_sent(sent)
                    
                last_ts = df.index[-1]
                state.last_candle = f"{last_ts:%Y-%m-%d %H:%M}"
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception(f"Ошибка в цикле проверки сигналов ({symbol})")
        await asyncio.sleep(config.CHECK_INTERVAL)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)
    data = BinanceData()

    # Запускаем по одному циклу на каждую пару
    tasks = [
        asyncio.create_task(signal_loop(bot, data, sym))
        for sym in config.SYMBOLS
    ]

    try:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
        except Exception as e:
            print(f"Не удалось удалить вебхук (возможно, проблемы с сетью): {e}")

        try:
            await bot.send_message(
                config.CHAT_ID,
                f"Бот запущен: {', '.join(config.SYMBOLS)} {config.TIMEFRAME}",
            )
        except Exception:
            logging.exception(
                "Не удалось отправить стартовое сообщение (проверьте CHAT_ID)"
            )

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
"""Точка входа: python main.py"""
import asyncio
import gc
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
PENDING_FILE = config.BASE_DIR / "pending_orders.json"

last_manage_time = 0
pending_orders = []

def load_sent() -> set[str]:
    try: return set(json.loads(SENT_FILE.read_text(encoding="utf-8")))
    except: return set()

def save_sent(sent: set[str]) -> None:
    SENT_FILE.write_text(json.dumps(sorted(sent)[-5000:]), encoding="utf-8")

def load_pending() -> list:
    try: return json.loads(PENDING_FILE.read_text(encoding="utf-8"))
    except: return []

def save_pending(pending: list) -> None:
    PENDING_FILE.write_text(json.dumps(pending), encoding="utf-8")

def format_price(price: float) -> str:
    if price == 0: return "0"
    if price < 0.001: return f"{price:,.6f}"
    if price < 1: return f"{price:,.5f}"
    if price < 100: return f"{price:,.3f}"
    return f"{price:,.2f}"

def format_signal(sig: Signal, symbol: str) -> str:
    is_long = sig.direction == "long"
    risk_pct = abs(sig.entry - sig.sl) / sig.entry * 100
    actual_rr = (sig.tp - sig.entry) / (sig.entry - sig.sl) if is_long else (sig.entry - sig.tp) / (sig.sl - sig.entry)
    return (
        f"{'🟢' if is_long else '🔴'} <b>{'LONG' if is_long else 'SHORT'} {symbol}</b> ({sig.timeframe})\n\n"
        f"Вход: <b>{format_price(sig.entry)}</b>\n"
        f"Stop-Loss: <b>{format_price(sig.sl)}</b>\n"
        f"Take-Profit: <b>{format_price(sig.tp)}</b>\n"
        f"R:R = 1:{actual_rr:.2f} | риск {risk_pct:.2f}%\n\n"
        f"Свеча ретеста (UTC): {sig.time:%Y-%m-%d %H:%M}"
    )

async def send_signal(bot: Bot, sig: Signal, symbol: str, trader: BybitTrader) -> None:
    text = format_signal(sig, symbol)
    try:
        await bot.send_message(config.CHAT_ID, text, parse_mode=ParseMode.HTML)
        state.last_signal_text = text
        state.signals_sent += 1
    except Exception as e:
        logging.error(f"❌ Ошибка ТГ: {e}")

    if config.BYBIT_API_KEY:
        order_info = await trader.create_trade(symbol, sig.direction, sig.entry, sig.sl, sig.tp)
        if order_info:
            pending_orders.append(order_info)
            save_pending(pending_orders)
            logging.info(f"📥 Ордер добавлен в очередь мониторинга: {order_info['raw_symbol']}")

last_signal_time = {}
last_ts_map = {}

async def process_symbol(bot: Bot, data: BinanceData, symbol: str, trader: BybitTrader) -> None:
    global last_manage_time
    try:
        if time.time() - last_manage_time > 900:
            await trader.manage_orders(symbol)
            last_manage_time = time.time()

        df_15m = await data.fetch_closed_candles(symbol, timeframe="15m")
        df_1h = await data.fetch_closed_candles(symbol, timeframe="1h")
        
        if df_15m.empty or df_1h.empty: return
        
        last_ts = last_ts_map.get(symbol)
        if last_ts is not None and df_15m.index[-1] <= last_ts: return
        
        ema20 = df_15m['Close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = df_15m['Close'].ewm(span=50, adjust=False).mean().iloc[-1]
        close = df_15m['Close'].iloc[-1]
        trend_15m = "long" if (close > ema20 and ema20 > ema50) else ("short" if (close < ema20 and ema20 < ema50) else "none")
        
        if trend_15m != "none":
            sigs = await asyncio.to_thread(analyze, df_15m, df_1h, config.SWING_PERIOD, config.RISK_REWARD, config.TICK_SIZE)
            sent = load_sent()
            
            for item in sigs:
                sig = item["signal"]
                if not item["is_valid"] or sig.direction != trend_15m: continue
                
                key = f"{symbol}_{sig.timeframe}_{sig.uid}"
                if key in sent: continue
                
                cooldown_key = f"{symbol}_{sig.timeframe}"
                if time.time() - last_signal_time.get(cooldown_key, 0) < 3600: continue
                
                if await trader.has_active_position_or_order(symbol):
                    continue
                
                await send_signal(bot, sig, symbol, trader)
                last_signal_time[cooldown_key] = time.time()
                sent.add(key)
                save_sent(sent)
            
            last_ts_map[symbol] = df_15m.index[-1]
    except Exception as e:
        logging.error(f"❌ Ошибка {symbol}: {e}")

async def batched_loop(bot: Bot, data: BinanceData, trader: BybitTrader) -> None:
    symbols = config.SYMBOLS
    batch_size = config.BATCH_SIZE
    current_index = 0
    while True:
        try:
            batch = symbols[current_index : current_index + batch_size]
            if not batch:
                current_index = 0
                batch = symbols[current_index : current_index + batch_size]
            
            logging.info(f"🔍 Проверка пачки ({len(batch)} пар): {', '.join(batch)}")
            for symbol in batch:
                await process_symbol(bot, data, symbol, trader)
            
            current_index = (current_index + batch_size) % len(symbols)
            gc.collect()
            state.last_candle = f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M}"
        except Exception:
            logging.exception("Ошибка в цикле")
        await asyncio.sleep(config.CHECK_INTERVAL)

async def monitor_pending_orders(trader: BybitTrader, bot: Bot):
    """Следит за лимитками, ставит стопы и проверяет "голые" позиции."""
    logging.info("👁️ Монитор ордеров запущен.")
    last_sanity_check = 0
    
    while True:
        # 1. Ставим стопы на новые исполненные ордера
        for item in pending_orders[:]:
            try:
                open_orders = await trader.exchange.fetch_open_orders(item['trade_symbol'])
                if any(o['id'] == item['order_id'] for o in open_orders):
                    continue # Ордер ещё висит
                
                positions = await trader.exchange.fetch_positions([item['raw_symbol'] + ':USDT'])
                if any(float(p.get('contracts', 0)) > 0 for p in positions):
                    if await trader.set_position_stops(item['raw_symbol'], item['sl'], item['tp']):
                        pending_orders.remove(item)
                        save_pending(pending_orders)
                        logging.info(f"✅ TP/SL установлены для {item['raw_symbol']}")
                else:
                    pending_orders.remove(item)
                    save_pending(pending_orders)
                    logging.info(f"🗑️ Ордер {item['raw_symbol']} отменен")
            except Exception as e:
                logging.error(f"Ошибка мониторинга: {e}")
        
        # 2. Sanity Check (раз в 10 минут)
        if time.time() - last_sanity_check > 600:
            last_sanity_check = time.time()
            try:
                positions = await trader.exchange.fetch_positions()
                for pos in positions:
                    if float(pos.get('contracts', 0)) > 0:
                        sl = pos.get('info', {}).get('stopLoss', '')
                        if not sl or float(sl) == 0:
                            raw_symbol = pos['symbol'].replace('/', '').split(':')[0]
                            if not any(o['raw_symbol'] == raw_symbol for o in pending_orders):
                                await bot.send_message(
                                    config.CHAT_ID, 
                                    f"🚨 <b>ВНИМАНИЕ! Позиция без стопов!</b>\n{raw_symbol} {pos['side']}\nПоставьте вручную!"
                                )
            except Exception as e:
                logging.error(f"Ошибка Sanity Check: {e}")
                
        await asyncio.sleep(30)

async def daily_report(bot: Bot):
    while True:
        now = datetime.now(timezone.utc)
        sec = (24 - now.hour - 1) * 3600 + (60 - now.minute - 1) * 60 + (60 - now.second)
        await asyncio.sleep(sec)
        try:
            await bot.send_message(config.CHAT_ID, f"📊 <b>Отчет</b>\nСигналов: {state.signals_sent}\nСвеча: {state.last_candle}")
        except: pass

async def start_health_server():
    app = web.Application()
    app.router.add_get('/health', lambda r: web.Response(text="OK"))
    port = int(os.getenv("PORT", 8080))
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', port).start()
    logging.info(f"🌐 Keep-Alive на порту {port}")

async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    await start_health_server()
    
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    data = BinanceData()
    trader = BybitTrader()

    global pending_orders
    pending_orders = load_pending()
    logging.info(f"📦 Загружено {len(pending_orders)} ордеров из кэша.")

    task = asyncio.create_task(batched_loop(bot, data, trader))
    report_task = asyncio.create_task(daily_report(bot))
    monitor_task = asyncio.create_task(monitor_pending_orders(trader, bot))

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await bot.send_message(config.CHAT_ID, f"Бот запущен (пар: {len(config.SYMBOLS)})")
        await dp.start_polling(bot)
    finally:
        for t in [task, report_task, monitor_task]: t.cancel()
        await asyncio.gather(task, report_task, monitor_task, return_exceptions=True)
        await data.close()
        await trader.close()
        await bot.session.close()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: pass

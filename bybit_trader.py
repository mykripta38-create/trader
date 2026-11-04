"""Модуль для автоматической торговли на Demo-счете Bybit."""
import ccxt.async_support as ccxt
import logging
import config
import time
import json
import asyncio
from pathlib import Path
from datetime import datetime, timezone

# Путь к файлу трекера возраста позиций
TRACKER_FILE = config.BASE_DIR / "positions_tracker.json"

class BybitTrader:
    def __init__(self):
        self.exchange = ccxt.bybit({
            'apiKey': config.BYBIT_API_KEY,
            'secret': config.BYBIT_API_SECRET,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',
            }
        })
        self.exchange.enableDemoTrading(True)
        logging.info("Bybit Trader инициализирован в режиме Demo.")

    async def get_balance(self) -> float:
        try:
            balance = await self.exchange.fetch_balance({'type': 'swap'})
            if 'USDT' in balance['free']:
                return float(balance['free']['USDT'])
            return 0.0
        except Exception as e:
            logging.error(f"❌ Ошибка получения баланса: {e}")
            return 0.0

    # ============================================================
    # 1. Создание сделки (только лимитный ордер, без TP/SL)
    # ============================================================
    async def create_trade(self, symbol: str, direction: str, entry: float, sl: float, tp: float):
        side = 'buy' if direction == 'long' else 'sell'
        trade_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        raw_symbol = symbol.replace('/', '')  # BTC/USDT -> BTCUSDT

        # --- Защита от дублирования ---
        try:
            positions = await self.exchange.fetch_positions([trade_symbol])
            for pos in positions:
                if float(pos['contracts']) > 0:
                    logging.info(f"⏭️ Пропуск {symbol}: уже есть открытая позиция.")
                    return None

            open_orders = await self.exchange.fetch_open_orders(trade_symbol)
            if open_orders:
                logging.info(f"⏭️ Пропуск {symbol}: уже есть активный ордер.")
                return None
        except Exception as e:
            logging.error(f"Ошибка проверки позиций перед входом: {e}")
        # -------------------------------------

        balance = await self.get_balance()
        if balance <= 0:
            logging.error("❌ Баланс равен 0 или не получен.")
            return None

        risk_amount = balance * (config.RISK_PERCENT / 100.0)
        stop_distance_pct = abs(entry - sl) / entry

        if stop_distance_pct == 0:
            logging.error("❌ Расстояние до стопа равно 0.")
            return None

        position_size_usdt = risk_amount / stop_distance_pct

        MAX_POSITION_USDT = 20000
        if position_size_usdt > MAX_POSITION_USDT:
            position_size_usdt = MAX_POSITION_USDT
            logging.warning(f"⚠️ Размер позиции {symbol} обрезан до {MAX_POSITION_USDT} USDT")

        amount = position_size_usdt / entry

        logging.info(f"📊 Риск: ${risk_amount:.2f}, Позиция: ${position_size_usdt:.2f} ({amount:.4f} монет)")

        try:
            # Создаем лимитный ордер (БЕЗ stopLoss/takeProfit)
            order = await self.exchange.create_order(
                symbol=trade_symbol,
                type='limit',
                side=side,
                amount=amount,
                price=entry,
                params={
                    'positionIdx': 0,
                }
            )
            logging.info(f"✅ Лимитный ордер создан: {trade_symbol} {side} {amount:.4f} @ {entry} (ID: {order['id']})")

            # Возвращаем метаданные для фонового мониторинга
            return {
                'order_id': order['id'],
                'raw_symbol': raw_symbol,
                'trade_symbol': trade_symbol,
                'sl': sl,
                'tp': tp,
                'direction': direction,
            }
        except Exception as e:
            logging.error(f"❌ Ошибка при создании ордера на Bybit: {e}")
            return None

    # ============================================================
    # 2. Установка TP/SL (с проверкой позиции и повторами)
    # ============================================================
    async def set_position_stops(self, raw_symbol: str, sl: float, tp: float):
        """
        Устанавливает TP/SL для существующей позиции с проверками и повторами.
        raw_symbol: 'BNBUSDT' (без слеша и :USDT)
        """
        # 1. Убеждаемся, что позиция реально существует
        for attempt in range(1, 6):  # до 5 попыток
            try:
                positions = await self.exchange.fetch_positions([raw_symbol + ':USDT'])
                active_pos = None
                for pos in positions:
                    if float(pos.get('contracts', 0)) > 0:
                        active_pos = pos
                        break

                if not active_pos:
                    logging.warning(f"⏳ Попытка {attempt}/5: позиция {raw_symbol} ещё не создана. Ждём 3 сек...")
                    await asyncio.sleep(3)
                    continue  # пробуем снова

                # 2. Позиция есть — отправляем запрос на установку TP/SL
                result = await self.exchange.private_post_v5_position_trading_stop({
                    'category': 'linear',
                    'symbol': raw_symbol,
                    'tpslMode': 'Full',
                    'positionIdx': 0,
                    'stopLoss': str(sl),
                    'takeProfit': str(tp),
                    'slTriggerBy': 'MarkPrice',
                    'tpTriggerBy': 'MarkPrice',
                })

                if result.get('retCode') == 0:
                    logging.info(f"✅ TP/SL установлены для {raw_symbol}: SL={sl}, TP={tp}")
                    return True
                else:
                    logging.error(f"❌ Bybit отклонил TP/SL для {raw_symbol}: retCode={result.get('retCode')}, retMsg={result.get('retMsg')}")
                    # Если ошибка связана с задержкой инициализации позиции
                    if result.get('retCode') in (34000, 30000):
                        await asyncio.sleep(3)
                        continue
                    else:
                        return False

            except Exception as e:
                logging.error(f"❌ Исключение при установке TP/SL для {raw_symbol} (попытка {attempt}): {e}")
                await asyncio.sleep(3)

        logging.error(f"❌ Не удалось установить TP/SL для {raw_symbol} после 5 попыток.")
        return False

    # ============================================================
    # 3. Смотритель ордеров (отмена зависших лимиток > 3 часов)
    # ============================================================
    async def manage_orders(self, symbol: str, max_age_hours: int = 3):
        trade_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        try:
            open_orders = await self.exchange.fetch_open_orders(trade_symbol)
            if not open_orders:
                return

            now = self.exchange.milliseconds()
            for order in open_orders:
                order_time = order.get('timestamp', now)
                age_hours = (now - order_time) / 3600000.0
                if age_hours > max_age_hours:
                    await self.exchange.cancel_order(order['id'], trade_symbol)
                    logging.info(f"🧹 Отменен зависший ордер {trade_symbol} (возраст {age_hours:.1f} ч.)")
        except Exception as e:
            logging.error(f"Ошибка отмены ордеров для {symbol}: {e}")

    # ============================================================
    # 4. Трекер зависших позиций (локальный учет времени)
    # ============================================================
    async def check_stale_positions(self, max_age_days: int = 3) -> list[dict]:
        stale = []
        try:
            positions = await self.exchange.fetch_positions()
            now_sec = time.time()
            
            # Загружаем локальную базу
            try:
                tracker = json.loads(TRACKER_FILE.read_text(encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                tracker = {}
                
            active_keys = set()
            
            for pos in positions:
                contracts = float(pos.get('contracts', 0) or 0)
                if contracts <= 0:
                    continue
                
                symbol = pos.get('symbol', 'unknown')
                side = pos.get('side', 'unknown')
                entry_price = pos.get('entryPrice', '0')
                
                # Уникальный ключ: Символ + Направление + Цена входа
                key = f"{symbol}_{side}_{entry_price}"
                active_keys.add(key)
                
                # Если бот видит позицию впервые — запоминаем время
                if key not in tracker:
                    tracker[key] = now_sec
                    logging.info(f"👁️ Взята на контроль новая позиция: {key}")
                
                # Считаем возраст от момента, когда бот её впервые увидел
                open_time = tracker[key]
                age_days = (now_sec - open_time) / 86400.0
                
                if age_days > max_age_days:
                    stale.append({
                        'symbol': symbol,
                        'side': side,
                        'contracts': contracts,
                        'age_days': round(age_days, 1),
                        'entry_price': entry_price,
                        'unrealized_pnl': pos.get('unrealizedPnl'),
                    })
            
            # Очищаем трекер от позиций, которые уже закрылись
            keys_to_remove = [k for k in tracker if k not in active_keys]
            for k in keys_to_remove:
                del tracker[k]
                logging.info(f"🧹 Позиция {k} закрыта, удалена из трекера возраста.")
            
            # Сохраняем обновленный трекер
            TRACKER_FILE.write_text(json.dumps(tracker, indent=2), encoding="utf-8")

            if stale:
                logging.warning(f"⚠️ Найдено {len(stale)} зависших позиций (> {max_age_days} дней)")
            return stale

        except Exception as e:
            logging.error(f"Ошибка проверки зависших позиций: {e}")
            return []

    async def close(self):
        await self.exchange.close()

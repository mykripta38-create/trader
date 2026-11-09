"""Модуль для автоматической торговли на Demo-счете Bybit."""
import ccxt.async_support as ccxt
import logging
import config

class BybitTrader:
    def __init__(self):
        self.exchange = ccxt.bybit({
            'apiKey': config.BYBIT_API_KEY,
            'secret': config.BYBIT_API_SECRET,
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'}
        })
        self.exchange.enableDemoTrading(True)
        logging.info("Bybit Trader инициализирован в режиме Demo.")

    async def get_balance(self) -> float:
        try:
            balance = await self.exchange.fetch_balance({'type': 'swap'})
            return float(balance['free'].get('USDT', 0.0))
        except Exception as e:
            logging.error(f"❌ Ошибка получения баланса: {e}")
            return 0.0

    async def create_trade(self, symbol: str, direction: str, entry: float, sl: float, tp: float):
        """Создает лимитный ордер СРАЗУ со стопом и тейком."""
        side = 'buy' if direction == 'long' else 'sell'
        trade_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol

        # Защита от дублей (проверка, нет ли уже позиции или ордера)
        try:
            positions = await self.exchange.fetch_positions([trade_symbol])
            if any(float(p['contracts']) > 0 for p in positions):
                logging.info(f"⏭️ Пропуск {symbol}: уже есть открытая позиция.")
                return None
            open_orders = await self.exchange.fetch_open_orders(trade_symbol)
            if open_orders:
                logging.info(f"⏭️ Пропуск {symbol}: уже есть активный ордер.")
                return None
        except Exception as e:
            logging.error(f"Ошибка проверки перед входом: {e}")

        balance = await self.get_balance()
        if balance <= 0: return None

        risk_amount = balance * (config.RISK_PERCENT / 100.0)
        stop_distance_pct = abs(entry - sl) / entry
        if stop_distance_pct == 0: return None

        position_size_usdt = min(risk_amount / stop_distance_pct, 20000)
        amount = position_size_usdt / entry

        try:
            # --- 1. ОКРУГЛЕНИЕ ЦЕН ДО ШАГА БИРЖИ (КРИТИЧНО ДЛЯ ДЕШЕВЫХ МОНЕТ) ---
            try:
                sl = float(self.exchange.price_to_precision(trade_symbol, sl))
                tp = float(self.exchange.price_to_precision(trade_symbol, tp))
                entry = float(self.exchange.price_to_precision(trade_symbol, entry))
            except Exception as e:
                logging.warning(f"⚠️ Не удалось округлить цены для {symbol}: {e}")
            # ---------------------------------------------------------------------

            # --- 2. СОЗДАНИЕ ОРДЕРА СРАЗУ С TP/SL ---
            order = await self.exchange.create_order(
                symbol=trade_symbol, 
                type='limit', 
                side=side,
                amount=amount, 
                price=entry, 
                params={
                    'positionIdx': 0,
                    'stopLoss': str(sl),
                    'takeProfit': str(tp),
                    'tpslMode': 'Full',
                    'tpOrderType': 'Market',
                    'slOrderType': 'Market',
                    'tpTriggerBy': 'MarkPrice',
                    'slTriggerBy': 'MarkPrice'
                }
            )
            
            # --- 3. ПРОВЕРКА: ПРИНЯЛА ЛИ БИРЖА СТОПЫ? ---
            order_info = order.get('info', {})
            if not order_info.get('stopLoss') and not order_info.get('takeProfit'):
                logging.warning(f"⚠️ ВНИМАНИЕ! Bybit создал ордер {symbol}, но ПРОИГНОРИРОВАЛ стопы! (возможно, проблема с точностью цен или Demo-счет глючит)")
            else:
                logging.info(f"✅ Ордер со стопами создан: {trade_symbol} {side} @ {entry} (SL={sl}, TP={tp})")
            
            return order
        except Exception as e:
            logging.error(f"❌ Ошибка создания ордера на Bybit: {e}")
            return None

    async def manage_orders(self, symbol: str, max_age_hours: int = 3):
        """Отменяет зависшие лимитки (> 3 часов)."""
        trade_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        try:
            open_orders = await self.exchange.fetch_open_orders(trade_symbol)
            now = self.exchange.milliseconds()
            for order in open_orders:
                age_hours = (now - order.get('timestamp', now)) / 3600000.0
                if age_hours > max_age_hours:
                    await self.exchange.cancel_order(order['id'], trade_symbol)
                    logging.info(f"🧹 Отменен зависший ордер {trade_symbol}")
        except Exception as e:
            logging.error(f"Ошибка отмены для {symbol}: {e}")

    async def close(self):
        await self.exchange.close()

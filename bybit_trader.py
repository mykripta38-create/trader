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
        side = 'buy' if direction == 'long' else 'sell'
        trade_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol

        # 1. Защита от дублей
        try:
            positions = await self.exchange.fetch_positions([trade_symbol])
            if any(float(p.get('contracts', 0)) > 0 for p in positions):
                logging.info(f"⏭️ Пропуск {symbol}: уже есть открытая позиция.")
                return None
            open_orders = await self.exchange.fetch_open_orders(trade_symbol)
            if open_orders:
                logging.info(f"⏭️ Пропуск {symbol}: уже есть активный ордер.")
                return None
        except Exception as e:
            logging.error(f"Ошибка проверки перед входом: {e}")

        # 2. Расчет размера позиции
        balance = await self.get_balance()
        if balance <= 0:
            return None

        risk_amount = balance * (config.RISK_PERCENT / 100.0)
        stop_distance_pct = abs(entry - sl) / entry
        if stop_distance_pct == 0:
            return None

        position_size_usdt = min(risk_amount / stop_distance_pct, 20000)
        amount = position_size_usdt / entry

        # Ограничение по лимиту контрактов (защита от ошибки 10001)
        try:
            market = self.exchange.market(trade_symbol)
            max_qty = market['limits']['amount']['max']
            if max_qty and amount > max_qty:
                logging.warning(f"⚠️ {symbol}: кол-во {amount:.2f} > лимита {max_qty}. Обрезано.")
                amount = max_qty
        except Exception:
            pass

        try:
            # 3. Создаем ордер СРАЗУ со стопом и тейком (как было раньше)
            order = await self.exchange.create_order(
                symbol=trade_symbol,
                type='limit',
                side=side,
                amount=amount,
                price=entry,
                params={
                    'stopLoss': {'triggerPrice': sl},
                    'takeProfit': {'triggerPrice': tp},
                    'positionIdx': 0
                }
            )
            logging.info(f"✅ Лимитный ордер создан: {trade_symbol} {side} {amount:.4f} @ {entry}")
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
        except Exception:
            pass

    async def close(self):
        await self.exchange.close()

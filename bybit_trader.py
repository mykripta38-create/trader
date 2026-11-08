"""Модуль для автоматической торговли на Demo-счете Bybit."""
import ccxt.async_support as ccxt
import logging
import asyncio
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

    async def has_active_position_or_order(self, symbol: str) -> bool:
        """Проверяет, есть ли уже открытая позиция или лимитный ордер по паре."""
        trade_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        try:
            positions = await self.exchange.fetch_positions([trade_symbol])
            if any(float(p.get('contracts', 0)) > 0 for p in positions):
                return True
                
            open_orders = await self.exchange.fetch_open_orders(trade_symbol)
            if open_orders:
                return True
            return False
        except Exception as e:
            logging.error(f"Ошибка проверки позиции/ордера для {symbol}: {e}")
            return False

    async def create_trade(self, symbol: str, direction: str, entry: float, sl: float, tp: float):
        side = 'buy' if direction == 'long' else 'sell'
        trade_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        raw_symbol = symbol.replace('/', '')

        if await self.has_active_position_or_order(symbol):
            logging.info(f"⏭️ Пропуск {symbol}: уже есть открытая позиция или ордер.")
            return None

        balance = await self.get_balance()
        if balance <= 0:
            return None

        risk_amount = balance * (config.RISK_PERCENT / 100.0)
        stop_distance_pct = abs(entry - sl) / entry
        if stop_distance_pct == 0:
            return None

        position_size_usdt = min(risk_amount / stop_distance_pct, 20000)
        amount = position_size_usdt / entry

        try:
            # Создаем лимитный ордер БЕЗ встроенных TP/SL (чтобы избежать багов CCXT)
            order = await self.exchange.create_order(
                symbol=trade_symbol, type='limit', side=side,
                amount=amount, price=entry, params={'positionIdx': 0}
            )
            logging.info(f"✅ Лимитный ордер создан: {trade_symbol} {side} {amount:.4f} @ {entry}")
            
            return {
                'order_id': order['id'],
                'raw_symbol': raw_symbol,
                'trade_symbol': trade_symbol,
                'sl': sl, 
                'tp': tp
            }
        except Exception as e:
            logging.error(f"❌ Ошибка создания ордера на Bybit: {e}")
            return None

    async def set_position_stops(self, raw_symbol: str, sl: float, tp: float):
        """Устанавливает TP/SL для существующей позиции с проверками."""
        for attempt in range(1, 4): # 3 попытки
            try:
                # Проверяем, видит ли биржа позицию
                positions = await self.exchange.fetch_positions([raw_symbol + ':USDT'])
                if not any(float(p.get('contracts', 0)) > 0 for p in positions):
                    await asyncio.sleep(3)
                    continue

                # Отправляем запрос на установку стопов
                result = await self.exchange.private_post_v5_position_trading_stop({
                    'category': 'linear', 
                    'symbol': raw_symbol,
                    'tpslMode': 'Full', 
                    'positionIdx': 0,
                    'stopLoss': str(sl), 
                    'takeProfit': str(tp),
                    'slTriggerBy': 'MarkPrice', 
                    'tpTriggerBy': 'MarkPrice',
                    'tpOrderType': 'Market', 
                    'slOrderType': 'Market'
                })
                
                if result.get('retCode') == 0:
                    logging.info(f"✅ TP/SL установлены для {raw_symbol}: SL={sl}, TP={tp}")
                    return True
                else:
                    logging.error(f"❌ Bybit отклонил TP/SL: {result.get('retMsg')}")
                    await asyncio.sleep(3)
            except Exception as e:
                logging.error(f"❌ Ошибка установки TP/SL для {raw_symbol}: {e}")
                await asyncio.sleep(3)
        return False

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

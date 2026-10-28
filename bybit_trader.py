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
            'options': {
                'defaultType': 'swap',  # Важно для фьючерсов
            }
        })
        self.exchange.enableDemoTrading(True)
        logging.info("Bybit Trader инициализирован в режиме Demo.")

    async def create_trade(self, symbol: str, direction: str, entry: float, sl: float, tp: float):
        side = 'buy' if direction == 'long' else 'sell'
        trade_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        
        try:
            amount = 10.0 / entry  # Тестовый объем 10 USDT
            
            order = await self.exchange.create_order(
                symbol=trade_symbol,
                type='limit',
                side=side,
                amount=amount,
                price=entry,
                params={
                    'stopLoss': {'triggerPrice': sl},
                    'takeProfit': {'triggerPrice': tp},
                    'positionIdx': 0  # 0 = Режим One-Way (односторонний)
                }
            )
            logging.info(f"✅ Ордер создан: {trade_symbol} {side} {amount:.4f} @ {entry}")
            return order
        except Exception as e:
            logging.error(f"❌ Ошибка при создании ордера на Bybit: {e}")
            return None

    async def manage_orders(self, symbol: str, max_age_hours: int = 3):
        """Отменяет зависшие лимитки и проверяет их актуальность."""
        trade_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        try:
            open_orders = await self.exchange.fetch_open_orders(trade_symbol)
            if not open_orders:
                return

            ticker = await self.exchange.fetch_ticker(trade_symbol)
            current_price = ticker['last']
            now = self.exchange.milliseconds()

            for order in open_orders:
                order_id = order['id']
                order_time = order.get('timestamp', now)
                age_hours = (now - order_time) / 3600000.0
                
                # Достаем стоп-лосс из параметров ордера
                sl_str = order.get('info', {}).get('stopLoss', '0')
                sl = float(sl_str) if sl_str else 0.0

                cancel_reason = None
                
                # 1. Проверка по времени (устарел)
                if age_hours > max_age_hours:
                    cancel_reason = f"висит {age_hours:.1f} ч. (лимит {max_age_hours} ч.)"
                
                # 2. Проверка по инвалидации цены (цена уже пробила SL)
                elif sl > 0:
                    if order['side'] == 'sell' and current_price > sl:
                        cancel_reason = f"цена ({current_price}) пробила SL ({sl}) до входа"
                    elif order['side'] == 'buy' and current_price < sl:
                        cancel_reason = f"цена ({current_price}) пробила SL ({sl}) до входа"

                if cancel_reason:
                    await self.exchange.cancel_order(order_id, trade_symbol)
                    logging.info(f"🧹 ОТМЕНЕН ОРДЕР {trade_symbol}: {cancel_reason}")

        except Exception as e:
            logging.error(f"❌ Ошибка смотрителя ордеров для {symbol}: {e}")

    async def check_positions_tpsl(self, symbol: str) -> list[str]:
        """Проверяет, что открытые позиции защищены SL и TP. Возвращает список предупреждений."""
        trade_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        warnings = []
        try:
            positions = await self.exchange.fetch_positions([trade_symbol])
            for pos in positions:
                # Если позиция открыта (есть контракты)
                if float(pos['contracts']) > 0:
                    sl = float(pos['info'].get('stopLoss', 0))
                    tp = float(pos['info'].get('takeProfit', 0))
                    
                    if sl == 0 or tp == 0:
                        msg = f"⚠️ <b>ВНИМАНИЕ!</b> Позиция {trade_symbol} открыта без SL или TP!\nВход: {pos['entryPrice']}, Размер: {pos['contracts']}"
                        logging.warning(msg)
                        warnings.append(msg)
        except Exception as e:
            logging.error(f"❌ Ошибка проверки позиций {symbol}: {e}")
            
        return warnings

    async def close(self):
        await self.exchange.close()

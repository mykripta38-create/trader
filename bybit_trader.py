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
                'defaultType': 'swap',
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
                    'positionIdx': 0
                }
            )
            logging.info(f"✅ Ордер создан: {trade_symbol} {side} {amount:.4f} @ {entry}")
            return order
        except Exception as e:
            logging.error(f"❌ Ошибка при создании ордера на Bybit: {e}")
            return None

    async def check_trailing_stop(self, symbol: str) -> list[str]:
        """Проверяет позиции и переводит стоп в безубыток при достижении 1:1.5."""
        trade_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        notifications = []
        try:
            positions = await self.exchange.fetch_positions([trade_symbol])
            for pos in positions:
                if float(pos['contracts']) > 0:
                    entry = float(pos['entryPrice'])
                    sl = float(pos['info'].get('stopLoss', 0))
                    tp = float(pos['info'].get('takeProfit', 0))
                    
                    if sl == 0 or tp == 0 or (pos['side'] == 'long' and sl >= entry) or (pos['side'] == 'short' and sl <= entry):
                        continue
                        
                    ticker = await self.exchange.fetch_ticker(trade_symbol)
                    current_price = ticker['last']
                    
                    if pos['side'] == 'long':
                        risk = entry - sl
                        profit = current_price - entry
                    else:
                        risk = sl - entry
                        profit = entry - current_price
                        
                    rr = profit / risk if risk > 0 else 0
                    
                    if rr >= config.TRAILING_TRIGGER_RR:
                        new_sl = entry
                        await self.exchange.set_trading_stop(
                            symbol=trade_symbol,
                            params={'stopLoss': new_sl, 'positionIdx': 0}
                        )
                        msg = (
                            f"🛡️ <b>Трейлинг-стоп сработал!</b>\n"
                            f"Пара: {trade_symbol}\n"
                            f"Профит достиг 1:{rr:.2f}. Стоп передвинут в безубыток ({new_sl})."
                        )
                        logging.info(msg)
                        notifications.append(msg)
        except Exception as e:
            logging.error(f"❌ Ошибка трейлинг-стопа для {symbol}: {e}")
            
        return notifications

    async def close(self):
        await self.exchange.close()

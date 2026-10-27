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
        })
        # Включаем режим Demo Trading
        self.exchange.enableDemoTrading(True)
        logging.info("Bybit Trader инициализирован в режиме Demo.")

    async def create_trade(self, symbol: str, direction: str, entry: float, sl: float, tp: float):
        """Выставляет лимитный ордер и стоп-лосс/тейк-профит."""
        side = 'buy' if direction == 'long' else 'sell'
        
        # Bybit требует формат 'BTC/USDT:USDT' для фьючерсов. 
        # Если в конфиге 'BTC/USDT', добавляем ':USDT'
        trade_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        
        try:
            # 1. Выставляем ордер на вход (лимитный)
            # Для простоты считаем размер позиции фиксированным (например, 10 USDT)
            # В реальности нужно считать от риска (риск / (entry - sl))
            amount = 10.0 / entry 
            
            order = await self.exchange.create_order(
                symbol=trade_symbol,
                type='limit',
                side=side,
                amount=amount,
                price=entry,
                params={
                    'stopLoss': {'triggerPrice': sl},
                    'takeProfit': {'triggerPrice': tp}
                }
            )
            logging.info(f"✅ Ордер на Bybit создан: {trade_symbol} {side} {amount} @ {entry}")
            return order
        except Exception as e:
            logging.error(f"❌ Ошибка при создании ордера на Bybit: {e}")
            return None

    async def close(self):
        await self.exchange.close()

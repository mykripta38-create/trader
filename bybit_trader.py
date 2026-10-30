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

    async def get_balance(self) -> float:
        try:
            balance = await self.exchange.fetch_balance({'type': 'swap'})
            if 'USDT' in balance['free']:
                return float(balance['free']['USDT'])
            return 0.0
        except Exception as e:
            logging.error(f"❌ Ошибка получения баланса: {e}")
            return 0.0

    async def create_trade(self, symbol: str, direction: str, entry: float, sl: float, tp: float):
        side = 'buy' if direction == 'long' else 'sell'
        trade_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        
        # --- ЗАЩИТА ОТ ДУБЛИРУЮЩИХ ОРДЕРОВ ---
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
        amount = position_size_usdt / entry
        
        logging.info(f"📊 Риск: ${risk_amount:.2f}, Позиция: ${position_size_usdt:.2f} ({amount:.4f} монет)")

        try:
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

    async def close(self):
        await self.exchange.close()

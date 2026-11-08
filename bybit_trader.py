async def _load_markets(self):
    try:
        await self.exchange.load_markets(True)
        logging.info("✅ Рынки Bybit обновлены.")
    except Exception as e:
        logging.error(f"❌ Ошибка загрузки рынков: {e}")

async def create_trade(self, symbol: str, direction: str, entry: float, sl: float, tp: float):
    # ... (проверка позиции/ордера) ...
    
    amount = position_size_usdt / entry
    
    # Ограничение по max_qty
    try:
        market = self.exchange.market(trade_symbol)
        max_qty = market['limits']['amount']['max']
        if max_qty and amount > max_qty:
            logging.warning(f"⚠️ {symbol}: кол-во {amount:.2f} > лимита {max_qty}. Обрезано.")
            amount = max_qty
    except Exception as e:
        logging.warning(f"Не удалось получить лимиты для {trade_symbol}: {e}")
    
    # ... (создание ордера) ...

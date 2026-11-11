"""Модуль для автоматической торговли на Demo-счете Bybit с гарантированной установкой TP/SL."""
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
        self._pending_checks = []  # список (raw_symbol, sl, tp) для фоновой проверки
        self._monitor_task = None
        logging.info("Bybit Trader инициализирован (с гарантией TP/SL).")

    async def start_monitor(self):
        """Запускает фоновую задачу, которая проверяет исполненные ордера и доставляет TP/SL."""
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_loop())
            logging.info("👁️ Фоновый монитор TP/SL запущен.")

    async def _monitor_loop(self):
        while True:
            for item in self._pending_checks[:]:
                raw_symbol, sl, tp = item
                try:
                    # Проверяем, есть ли открытая позиция по символу
                    positions = await self.exchange.fetch_positions([raw_symbol + ':USDT'])
                    active = None
                    for pos in positions:
                        if float(pos.get('contracts', 0)) > 0:
                            active = pos
                            break
                    
                    if active:
                        # Позиция есть — проверяем, установлены ли TP/SL
                        stop_loss = active.get('info', {}).get('stopLoss', '')
                        take_profit = active.get('info', {}).get('takeProfit', '')
                        
                        if not stop_loss or float(stop_loss) == 0 or not take_profit or float(take_profit) == 0:
                            logging.warning(f"⚠️ Позиция {raw_symbol} без TP/SL. Принудительная установка...")
                            await self.set_position_stops(raw_symbol, sl, tp)
                        
                        self._pending_checks.remove(item)
                except Exception as e:
                    logging.error(f"Ошибка в мониторе TP/SL для {raw_symbol}: {e}")
            
            await asyncio.sleep(30)  # проверка каждые 30 секунд

    async def set_position_stops(self, raw_symbol: str, sl: float, tp: float):
        """Принудительно устанавливает TP/SL для существующей позиции."""
        try:
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
                logging.info(f"✅ TP/SL принудительно установлены для {raw_symbol}: SL={sl}, TP={tp}")
                return True
            else:
                logging.error(f"❌ Bybit отклонил TP/SL для {raw_symbol}: {result.get('retMsg')}")
                return False
        except Exception as e:
            logging.error(f"❌ Ошибка установки TP/SL для {raw_symbol}: {e}")
            return False

    async def get_balance(self) -> float:
        try:
            balance = await self.exchange.fetch_balance({'type': 'swap'})
            return float(balance['free'].get('USDT', 0.0))
        except Exception as e:
            logging.error(f"❌ Ошибка получения баланса: {e}")
            return 0.0

    async def create_trade(self, symbol: str, direction: str, entry: float, sl: float, tp: float):
        """Создаёт лимитный ордер с TP/SL и добавляет его в фоновую проверку."""
        side = 'buy' if direction == 'long' else 'sell'
        trade_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        raw_symbol = symbol.replace('/', '')

        # Защита от дублей
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
        if balance <= 0:
            return None

        risk_amount = balance * (config.RISK_PERCENT / 100.0)
        stop_distance_pct = abs(entry - sl) / entry
        if stop_distance_pct == 0:
            return None

        position_size_usdt = min(risk_amount / stop_distance_pct, 20000)
        amount = position_size_usdt / entry

        # Округление цен до шага биржи (критично для дешёвых монет)
        try:
            entry = float(self.exchange.price_to_precision(trade_symbol, entry))
            sl = float(self.exchange.price_to_precision(trade_symbol, sl))
            tp = float(self.exchange.price_to_precision(trade_symbol, tp))
        except Exception as e:
            logging.warning(f"Не удалось округлить цены для {symbol}: {e}")

        try:
            # Создаём лимитный ордер с TP/SL
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
            logging.info(f"✅ Лимитный ордер создан: {trade_symbol} {side} @ {entry} (с TP/SL)")

            # Добавляем в очередь на проверку
            self._pending_checks.append((raw_symbol, sl, tp))
            await self.start_monitor()

            return order
        except Exception as e:
            logging.error(f"❌ Ошибка создания ордера на Bybit: {e}")
            return None

    async def manage_orders(self, symbol: str, max_age_hours: int = 3):
        """Отменяет зависшие лимитки."""
        trade_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        try:
            open_orders = await self.exchange.fetch_open_orders(trade_symbol)
            now = self.exchange.milliseconds()
            for order in open_orders:
                age_hours = (now - order.get('timestamp', now)) / 3600000.0
                if age_hours > max_age_hours:
                    await self.exchange.cancel_order(order['id'], trade_symbol)
                    logging.info(f"🧹 Отменён зависший ордер {trade_symbol}")
        except Exception as e:
            logging.error(f"Ошибка отмены для {symbol}: {e}")

    async def close(self):
        if self._monitor_task:
            self._monitor_task.cancel()
        await self.exchange.close()

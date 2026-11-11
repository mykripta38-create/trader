"""Модуль для автоматической торговли на Demo-счете Bybit с гарантией TP/SL."""
import ccxt.async_support as ccxt
import logging
import asyncio
import json
import config
from pathlib import Path

PENDING_FILE = config.BASE_DIR / "pending_stops.json"

class BybitTrader:
    def __init__(self):
        self.exchange = ccxt.bybit({
            'apiKey': config.BYBIT_API_KEY,
            'secret': config.BYBIT_API_SECRET,
            'enableRateLimit': True,
            'options': {'defaultType': 'swap'}
        })
        self.exchange.enableDemoTrading(True)
        self._pending_checks = self._load_pending()
        self._monitor_task = None
        logging.info(f"Bybit Trader инициализирован. В очереди на проверку TP/SL: {len(self._pending_checks)}")

    def _load_pending(self) -> list:
        try:
            return json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save_pending(self):
        PENDING_FILE.write_text(json.dumps(self._pending_checks), encoding="utf-8")

    async def start_monitor(self):
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_loop())
            logging.info("👁️ Фоновый монитор TP/SL запущен.")

    async def _monitor_loop(self):
        while True:
            # 1. Проверяем очередь из памяти (и из файла)
            for item in self._pending_checks[:]:
                raw_symbol, sl, tp = item
                try:
                    positions = await self.exchange.fetch_positions([raw_symbol + ':USDT'])
                    active = next((p for p in positions if float(p.get('contracts', 0)) > 0), None)
                    
                    if active:
                        # Позиция открыта. Есть ли стопы?
                        stop_loss = active.get('info', {}).get('stopLoss', '')
                        take_profit = active.get('info', {}).get('takeProfit', '')
                        
                        if not stop_loss or float(stop_loss) == 0 or not take_profit or float(take_profit) == 0:
                            logging.warning(f"⚠️ Позиция {raw_symbol} без TP/SL. Принудительная установка...")
                            success = await self.set_position_stops(raw_symbol, sl, tp)
                            if success:
                                self._pending_checks.remove(item)
                                self._save_pending()
                        else:
                            # Стопы уже стоят (Bybit сам перенес их с ордера)
                            self._pending_checks.remove(item)
                            self._save_pending()
                    else:
                        # Позиции нет. Проверяем, висит ли еще ордер?
                        open_orders = await self.exchange.fetch_open_orders(raw_symbol + ':USDT')
                        if not any(o['id'] == item.get('order_id') for o in open_orders):
                            # Ни ордера, ни позиции — значит, отменился
                            self._pending_checks.remove(item)
                            self._save_pending()
                except Exception as e:
                    logging.error(f"Ошибка в мониторе TP/SL для {raw_symbol}: {e}")
            
            await asyncio.sleep(30)

    async def set_position_stops(self, raw_symbol: str, sl: float, tp: float):
        try:
            result = await self.exchange.private_post_v5_position_trading_stop({
                'category': 'linear', 'symbol': raw_symbol,
                'tpslMode': 'Full', 'positionIdx': 0,
                'stopLoss': str(sl), 'takeProfit': str(tp),
                'slTriggerBy': 'MarkPrice', 'tpTriggerBy': 'MarkPrice',
                'tpOrderType': 'Market', 'slOrderType': 'Market'
            })
            if result.get('retCode') == 0:
                logging.info(f"✅ TP/SL установлены для {raw_symbol}: SL={sl}, TP={tp}")
                return True
            else:
                logging.error(f"❌ Bybit отклонил: {result.get('retMsg')}")
                return False
        except Exception as e:
            logging.error(f"❌ Ошибка установки TP/SL для {raw_symbol}: {e}")
            return False

    async def get_balance(self) -> float:
        try:
            balance = await self.exchange.fetch_balance({'type': 'swap'})
            return float(balance['free'].get('USDT', 0.0))
        except Exception as e:
            return 0.0

    async def create_trade(self, symbol: str, direction: str, entry: float, sl: float, tp: float):
        side = 'buy' if direction == 'long' else 'sell'
        trade_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        raw_symbol = symbol.replace('/', '')

        try:
            positions = await self.exchange.fetch_positions([trade_symbol])
            if any(float(p['contracts']) > 0 for p in positions): return None
            open_orders = await self.exchange.fetch_open_orders(trade_symbol)
            if open_orders: return None
        except Exception as e:
            logging.error(f"Ошибка проверки: {e}")

        balance = await self.get_balance()
        if balance <= 0: return None

        risk_amount = balance * (config.RISK_PERCENT / 100.0)
        stop_distance_pct = abs(entry - sl) / entry
        if stop_distance_pct == 0: return None

        position_size_usdt = min(risk_amount / stop_distance_pct, 20000)
        amount = position_size_usdt / entry

        try:
            entry = float(self.exchange.price_to_precision(trade_symbol, entry))
            sl = float(self.exchange.price_to_precision(trade_symbol, sl))
            tp = float(self.exchange.price_to_precision(trade_symbol, tp))
        except: pass

        try:
            order = await self.exchange.create_order(
                symbol=trade_symbol, type='limit', side=side, amount=amount, price=entry,
                params={
                    'positionIdx': 0,
                    'stopLoss': str(sl), 'takeProfit': str(tp),
                    'tpslMode': 'Full', 'tpOrderType': 'Market', 'slOrderType': 'Market',
                    'tpTriggerBy': 'MarkPrice', 'slTriggerBy': 'MarkPrice'
                }
            )
            logging.info(f"✅ Ордер создан: {trade_symbol} @ {entry} (с TP/SL)")
            
            # ДОБАВЛЯЕМ В ФАЙЛ
            self._pending_checks.append([raw_symbol, sl, tp])
            self._save_pending()
            await self.start_monitor()

            return order
        except Exception as e:
            logging.error(f"❌ Ошибка создания: {e}")
            return None

    async def manage_orders(self, symbol: str, max_age_hours: int = 3):
        trade_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        try:
            open_orders = await self.exchange.fetch_open_orders(trade_symbol)
            now = self.exchange.milliseconds()
            for order in open_orders:
                age_hours = (now - order.get('timestamp', now)) / 3600000.0
                if age_hours > max_age_hours:
                    try:
                        await self.exchange.cancel_order(order['id'], trade_symbol)
                        logging.info(f"🧹 Отменен зависший ордер {trade_symbol}")
                    except Exception as e:
                        if "110001" in str(e): pass # Ордер уже исполнен
                        else: logging.error(f"Ошибка отмены: {e}")
        except Exception as e:
            pass

    async def close(self):
        if self._monitor_task:
            self._monitor_task.cancel()
        await self.exchange.close()

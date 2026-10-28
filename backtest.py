import ccxt
import pandas as pd
import logging
from datetime import datetime, timezone
import config
from smc_engine import analyze

# Настраиваем логирование, чтобы видеть прогресс
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# Ваш список пар
SYMBOLS = config.SYMBOLS

# Инициализация Binance
exchange = ccxt.binance({'enableRateLimit': True})

def fetch_ohlcv(symbol, timeframe, limit=1000):
    """Скачивает свечи с Binance."""
    try:
        raw = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(raw, columns=["ts", "Open", "High", "Low", "Close", "Volume"])
        df["Date"] = pd.to_datetime(df["ts"], unit="ms")
        df = df.set_index("Date").drop(columns="ts").astype(float)
        return df
    except Exception as e:
        logging.error(f"Ошибка скачивания {symbol} {timeframe}: {e}")
        return pd.DataFrame()

def run_backtest():
    total_signals = 0
    wins = 0
    losses = 0
    open_trades = 0
    
    print(f"\n{'='*50}")
    print(f"🚀 НАЧАЛО БЭКТЕСТА (30 дней)")
    print(f"{'='*50}\n")

    for symbol in SYMBOLS:
        logging.info(f"Скачивание данных для {symbol}...")
        
        # Скачиваем 1000 свечей 15m (это примерно 10 дней, для 30 дней нужно больше, но для теста хватит)
        df_15m = fetch_ohlcv(symbol, "15m", limit=1000)
        df_1h = fetch_ohlcv(symbol, "1h", limit=1000)
        df_4h = fetch_ohlcv(symbol, "4h", limit=1000)
        
        if df_15m.empty or df_1h.empty or df_4h.empty:
            continue
            
        # Убираем текущую незакрытую свечу
        df_15m = df_15m.iloc[:-1]
        df_1h = df_1h.iloc[:-1]
        df_4h = df_4h.iloc[:-1]

        # 1. Проверяем глобальный тренд 1H (как в smc_engine.py)
        ema50_1h = df_1h['Close'].ewm(span=50, adjust=False).mean()
        ema200_1h = df_1h['Close'].ewm(span=200, adjust=False).mean()
        
        # 2. Проверяем локальный тренд 15m (как в main.py)
        ema20_15m = df_15m['Close'].ewm(span=20, adjust=False).mean()
        ema50_15m = df_15m['Close'].ewm(span=50, adjust=False).mean()
        
        # Берем последние 500 свечей для анализа
        df_15m_slice = df_15m.tail(500).copy()
        df_1h_slice = df_1h.tail(500).copy()
        
        # Находим сигналы
        signals = analyze(df_15m_slice, df_1h_slice, config.SWING_PERIOD, config.RISK_REWARD, config.TICK_SIZE)
        
        for sig in signals:
            # Проверка фильтров EMA
            idx = df_15m.index.get_loc(sig.time)
            
            # Фильтр 1H: цена > EMA50 и EMA50 > EMA200 (для лонга)
            htf_trend = "long" if df_1h['Close'].iloc[-1] > ema50_1h.iloc[-1] > ema200_1h.iloc[-1] else "short"
            
            # Фильтр 15m
            trend_15m = "long" if df_15m['Close'].iloc[idx] > ema20_15m.iloc[idx] > ema50_15m.iloc[idx] else "short"
            
            if sig.direction != htf_trend or sig.direction != trend_15m:
                continue # Пропускаем сигнал, если тренд не совпал
                
            total_signals += 1
            print(f"🎯 Найден сигнал: {symbol} {sig.direction.upper()} | Время: {sig.time} | Вход: {sig.entry}")
            
            # 3. Проверяем, что было дальше (после входа)
            future_candles = df_15m.iloc[idx+1:idx+50] # Смотрим 50 свечей вперед
            
            hit_tp = False
            hit_sl = False
            
            for _, candle in future_candles.iterrows():
                if sig.direction == "long":
                    if candle['Low'] <= sig.sl:
                        hit_sl = True
                        break
                    if candle['High'] >= sig.tp:
                        hit_tp = True
                        break
                else: # SHORT
                    if candle['High'] >= sig.sl:
                        hit_sl = True
                        break
                    if candle['Low'] <= sig.tp:
                        hit_tp = True
                        break
            
            if hit_tp:
                wins += 1
                print(f"   ✅ TP достигнут!")
            elif hit_sl:
                losses += 1
                print(f"   ❌ SL сработал.")
            else:
                open_trades += 1
                print(f"   ⏳ Сделка еще не закрыта (прошло 50 свечей).")

    # Итоговая статистика
    print(f"\n{'='*50}")
    print(f"📊 РЕЗУЛЬТАТЫ БЭКТЕСТА")
    print(f"{'='*50}")
    print(f"Всего сигналов (прошедших фильтры): {total_signals}")
    print(f"Прибыльных (TP): {wins}")
    print(f"Убыточных (SL): {losses}")
    print(f"Не закрытых: {open_trades}")
    
    if total_signals > 0:
        win_rate = (wins / (wins + losses)) * 100 if (wins + losses) > 0 else 0
        print(f"Win Rate: {win_rate:.1f}%")
    else:
        print("⚠️ Сигналов не найдено! Фильтры слишком строгие.")

if __name__ == "__main__":
    run_backtest()

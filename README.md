# Market Neutral Pairs Trading Bot

Рыночно-нейтральный бот для парного трейдинга (Statistical Arbitrage) на фьючерсах криптовалютных бирж.

## Стек

- Python 3.10+
- CCXT (Binance/Bybit Futures)
- Pandas, Pandas_TA, Numpy, SciPy

## Установка

```bash
pip install -r requirements.txt
cp .env.example .env
# Отредактируйте .env — добавьте API ключи и пары
```

## Конфигурация (.env)

| Переменная | Описание |
|------------|----------|
| EXCHANGE | `bybit` или `binance` |
| EXCHANGE_BASE_URL | URL API (для демо Bybit: `https://api-demo.bybit.com`) |
| BYBIT_API_KEY / BYBIT_API_SECRET | Ключи Bybit |
| LEVERAGE | Плечо (макс. 5) |
| RISK_PER_TRADE_PCT | Риск на сделку, % |
| MAX_BASKET_RISK_PCT | Макс. риск портфеля, % |
| PAIRS_JSON | Пары по секторам (JSON) |

## Запуск

```bash
python main.py
```

## Структура

- `exchange_handler.py` — ExchangeHandler: работа с биржей через CCXT (async)
- `strategy_manager.py` — StrategyManager: Z-Score, EMA, RSI, OI
- `risk_manager.py` — RiskManager: leverage, sizing, funding, trailing stop
- `bot.py` — PairsTradingBot: оркестрация, логирование
- `config.py` — загрузка настроек из .env

## Логирование

- Консоль: все действия
- `trade_log.csv`: сделки (timestamp, action, sector, symbols, amounts, zscore, reason)

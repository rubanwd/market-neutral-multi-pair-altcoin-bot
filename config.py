"""Конфигурация бота из .env"""
import os
import json
from itertools import combinations
from typing import Dict, List
from dotenv import load_dotenv

load_dotenv()

# Exchange
EXCHANGE = os.getenv("EXCHANGE", "bybit")
EXCHANGE_BASE_URL = os.getenv("EXCHANGE_BASE_URL", "https://api-demo.bybit.com")
BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET", "")
# DRY_RUN: без API ключей, только анализ, без реальных ордеров (для тестов)
DRY_RUN = os.getenv("DRY_RUN", "true").lower() in ("1", "true", "yes")
DRY_RUN_EQUITY = float(os.getenv("DRY_RUN_EQUITY", "10000"))
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "")

# Risk
LEVERAGE = int(os.getenv("LEVERAGE", "5"))
RISK_PER_TRADE_PCT = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
MAX_BASKET_RISK_PCT = float(os.getenv("MAX_BASKET_RISK_PCT", "40.0"))
MAX_FUNDING_RATE_PCT = float(os.getenv("MAX_FUNDING_RATE_PCT", "0.06"))
TRAILING_STOP_PCT = float(os.getenv("TRAILING_STOP_PCT", "1.5"))

# Strategy (~5 сделок/день: более мягкий вход)
ZSCORE_ENTRY_THRESHOLD = float(os.getenv("ZSCORE_ENTRY_THRESHOLD", "1.2"))
ZSCORE_EXIT_TP = float(os.getenv("ZSCORE_EXIT_TP", "0.0"))
ZSCORE_EXIT_SL = float(os.getenv("ZSCORE_EXIT_SL", "3.0"))
ZSCORE_TRAILING_ACTIVATION = float(os.getenv("ZSCORE_TRAILING_ACTIVATION", "0.5"))

# Фильтры (false = отключить для большего числа сигналов)
USE_EMA_FILTER = os.getenv("USE_EMA_FILTER", "false").lower() in ("1", "true", "yes")
USE_RSI_FILTER = os.getenv("USE_RSI_FILTER", "false").lower() in ("1", "true", "yes")
USE_OI_FILTER = os.getenv("USE_OI_FILTER", "false").lower() in ("1", "true", "yes")

# Technical
ZSCORE_WINDOW = int(os.getenv("ZSCORE_WINDOW", "20"))
TIMEFRAME = os.getenv("TIMEFRAME", "1h")
EMA_PERIODS = [int(x) for x in os.getenv("EMA_PERIODS", "20,50,100,200").split(",")]
RUN_INTERVAL_SEC = int(os.getenv("RUN_INTERVAL_SEC", "180"))


def get_pairs_by_sector() -> Dict[str, List[List[str]]]:
    """
    Парсит пары по секторам из PAIRS_JSON.
    Форматы:
    - {"L1": ["SOL/USDT:USDT", "AVAX/USDT:USDT"], ...} — одна пара
    - {"L1": [["SOL/USDT:USDT", "AVAX/USDT:USDT"], ["NEAR/USDT:USDT", "ATOM/USDT:USDT"]], ...} — явные пары
    - {"L1": ["SOL/USDT:USDT", "AVAX/USDT:USDT", "NEAR/USDT:USDT", "ATOM/USDT:USDT"]} — автогенерация всех пар
    """
    default_pairs = (
        '{"L1": ["SOL/USDT:USDT", "AVAX/USDT:USDT", "NEAR/USDT:USDT", "ATOM/USDT:USDT", "INJ/USDT:USDT", "SUI/USDT:USDT"], '
        '"L2": ["ARB/USDT:USDT", "OP/USDT:USDT", "IMX/USDT:USDT", "STRK/USDT:USDT", "LINK/USDT:USDT", "DOT/USDT:USDT"], '
        '"L3": ["POL/USDT:USDT", "UNI/USDT:USDT", "AAVE/USDT:USDT", "LDO/USDT:USDT", "CRV/USDT:USDT", "COMP/USDT:USDT"], '
        '"L4": ["APT/USDT:USDT", "FIL/USDT:USDT", "1INCH/USDT:USDT", "SAND/USDT:USDT", "1000PEPE/USDT:USDT", "WIF/USDT:USDT"]}'
    )
    raw = os.getenv("PAIRS_JSON", default_pairs)
    try:
        data = json.loads(raw)
        result = {}
        for sector, val in data.items():
            if isinstance(val[0], list):
                result[sector] = val
            elif len(val) >= 2:
                if len(val) == 2:
                    result[sector] = [val]
                else:
                    result[sector] = [list(p) for p in combinations(val, 2)]
            else:
                result[sector] = []
        return result
    except (json.JSONDecodeError, (IndexError, TypeError)):
        return {
            "L1": [["SOL/USDT:USDT", "AVAX/USDT:USDT"], ["SOL/USDT:USDT", "NEAR/USDT:USDT"], ["AVAX/USDT:USDT", "NEAR/USDT:USDT"]],
            "L2": [["ARB/USDT:USDT", "OP/USDT:USDT"]],
        }

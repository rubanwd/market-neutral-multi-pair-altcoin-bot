"""
Главный модуль бота — оркестрация ExchangeHandler, StrategyManager, RiskManager.
Асинхронный мониторинг 10+ пар по секторам.
"""
import asyncio
import csv
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import config
from exchange_handler import ExchangeHandler
from strategy_manager import StrategyManager
from risk_manager import RiskManager

logger = logging.getLogger(__name__)
TRADE_LOG_PATH = Path("trade_log.csv")


def setup_logging() -> None:
    """Настройка логирования в консоль и файл."""
    log_format = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def log_trade(
    action: str,
    sector: str,
    symbol1: str,
    symbol2: str,
    side1: str,
    side2: str,
    amount1: float,
    amount2: float,
    price1: float,
    price2: float,
    zscore: float,
    reason: str = "",
) -> None:
    """Записывает сделку в trade_log.csv."""
    row = {
        "timestamp": datetime.utcnow().isoformat(),
        "action": action,
        "sector": sector,
        "symbol1": symbol1,
        "symbol2": symbol2,
        "side1": side1,
        "side2": side2,
        "amount1": amount1,
        "amount2": amount2,
        "price1": price1,
        "price2": price2,
        "zscore": zscore,
        "reason": reason,
    }
    file_exists = TRADE_LOG_PATH.exists()
    with open(TRADE_LOG_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=row.keys())
        if not file_exists:
            w.writeheader()
        w.writerow(row)
    logger.info("Trade logged: %s %s %s/%s", action, sector, symbol1, symbol2)


class PairsTradingBot:
    """Бот парного трейдинга."""

    def __init__(self):
        self.exchange = ExchangeHandler()
        self.strategy = StrategyManager()
        self.risk = RiskManager()
        self.pairs_by_sector = config.get_pairs_by_sector()
        self._oi_cache: Dict[str, float] = {}

    async def fetch_ohlcv_safe(self, symbol: str) -> Optional[object]:
        """Безопасное получение OHLCV с retry."""
        for attempt in range(3):
            try:
                return await self.exchange.fetch_ohlcv(
                    symbol,
                    config.TIMEFRAME,
                    limit=config.ZSCORE_WINDOW + 50,
                )
            except asyncio.TimeoutError:
                logger.warning("Timeout fetching %s (attempt %d)", symbol, attempt + 1)
            except Exception as e:
                logger.error("OHLCV error %s: %s", symbol, e)
            await asyncio.sleep(1)
        return None

    async def analyze_sector(self, sector: str, symbols: List[str]) -> Optional[Dict]:
        """Анализирует одну пару в секторе (первые два символа)."""
        if len(symbols) < 2:
            return None
        s1, s2 = symbols[0], symbols[1]
        df1 = await self.fetch_ohlcv_safe(s1)
        df2 = await self.fetch_ohlcv_safe(s2)
        if df1 is None or df2 is None or len(df1) < config.ZSCORE_WINDOW or len(df2) < config.ZSCORE_WINDOW:
            return None

        oi1 = await self.exchange.fetch_open_interest(s1)
        oi2 = await self.exchange.fetch_open_interest(s2)
        oi1_prev = self._oi_cache.get(f"{s1}_prev")
        oi2_prev = self._oi_cache.get(f"{s2}_prev")
        self._oi_cache[f"{s1}_prev"] = oi1
        self._oi_cache[f"{s2}_prev"] = oi2

        result = self.strategy.analyze_pair(
            df1, df2, s1, s2,
            oi1=oi1, oi1_prev=oi1_prev,
            oi2=oi2, oi2_prev=oi2_prev,
        )
        result["sector"] = sector
        result["symbol1"] = s1
        result["symbol2"] = s2
        return result

    async def check_funding_and_execute(
        self,
        analysis: Dict,
        equity: float,
        positions_value: float,
    ) -> bool:
        """Проверяет funding rate и выполняет вход при соблюдении условий."""
        signal = analysis.get("signal")
        if not signal or not analysis.get("ema_ok") or not analysis.get("rsi_ok") or not analysis.get("oi_ok"):
            return False

        s1, s2 = analysis["symbol1"], analysis["symbol2"]
        fr1 = await self.exchange.fetch_funding_rate(s1)
        fr2 = await self.exchange.fetch_funding_rate(s2)
        side1 = "buy" if signal == "long_short" else "sell"
        side2 = "sell" if signal == "long_short" else "buy"
        if not self.risk.check_funding_rate(fr1 or 0, side1) or not self.risk.check_funding_rate(fr2 or 0, side2):
            logger.info("Funding rate filter: skip %s/%s", s1, s2)
            return False

        t1 = await self.exchange.fetch_ticker(s1)
        t2 = await self.exchange.fetch_ticker(s2)
        price1 = float(t1.get("last", 0) or 0)
        price2 = float(t2.get("last", 0) or 0)
        if price1 <= 0 or price2 <= 0:
            return False

        size1 = self.risk.calc_position_size(equity, price1)
        size2 = self.risk.calc_position_size(equity, price2)
        trade_value = size1 * price1 + size2 * price2
        if not self.risk.check_basket_risk(equity, positions_value, trade_value):
            logger.info("Basket risk limit: skip %s/%s", s1, s2)
            return False

        try:
            await self.exchange.set_leverage(s1, config.LEVERAGE)
            await self.exchange.set_leverage(s2, config.LEVERAGE)
            await self.exchange.create_market_order(s1, side1, size1)
            await self.exchange.create_market_order(s2, side2, size2)
            pair_id = f"{analysis['sector']}_{s1}_{s2}"
            self.risk.register_position(pair_id, s1, side1, price1, analysis["zscore"], size1)
            self.risk.register_position(pair_id + "_2", s2, side2, price2, analysis["zscore"], size2)
            log_trade(
                "OPEN", analysis["sector"], s1, s2,
                side1, side2, size1, size2, price1, price2,
                analysis["zscore"], "entry",
            )
            return True
        except Exception as e:
            logger.error("Execute error: %s", e)
            return False

    async def check_exits_and_trailing(
        self,
        analysis: Dict,
        pair_id: str,
    ) -> bool:
        """Проверяет выход (TP/SL) и trailing stop. Возвращает True если позиция закрыта."""
        exit_reason = analysis.get("exit")
        s1, s2 = analysis["symbol1"], analysis["symbol2"]
        zscore = analysis["zscore"]

        if exit_reason:
            # Закрыть по TP/SL
            positions = await self.exchange.fetch_positions()
            for p in positions:
                sym = p.get("symbol")
                if sym in (s1, s2):
                    contracts = float(p.get("contracts", 0) or 0)
                    side = p.get("side", "buy")
                    if contracts > 0:
                        await self.exchange.close_position(sym, side, contracts)
            self.risk.remove_position(pair_id)
            self.risk.remove_position(pair_id + "_2")
            log_trade("CLOSE", analysis["sector"], s1, s2, "", "", 0, 0, 0, 0, zscore, exit_reason)
            return True

        # Trailing stop
        t1 = await self.exchange.fetch_ticker(s1)
        t2 = await self.exchange.fetch_ticker(s2)
        price1 = float(t1.get("last", 0) or 0)
        price2 = float(t2.get("last", 0) or 0)
        if self.risk.update_trailing(pair_id, price1, zscore) or self.risk.update_trailing(pair_id + "_2", price2, zscore):
            positions = await self.exchange.fetch_positions()
            for p in positions:
                sym = p.get("symbol")
                if sym in (s1, s2):
                    contracts = float(p.get("contracts", 0) or 0)
                    side = p.get("side", "buy")
                    if contracts > 0:
                        await self.exchange.close_position(sym, side, contracts)
            self.risk.remove_position(pair_id)
            self.risk.remove_position(pair_id + "_2")
            log_trade("CLOSE", analysis["sector"], s1, s2, "", "", 0, 0, price1, price2, zscore, "trailing_stop")
            return True
        return False

    async def run_cycle(self) -> None:
        """Один цикл мониторинга всех пар."""
        try:
            equity = await self.exchange.get_equity_usdt()
            positions = await self.exchange.fetch_positions()
            positions_value = sum(
                abs(float(p.get("contractSize", 1) or 1) * float(p.get("contracts", 0) or 0) * float(p.get("markPrice", 0) or 0))
                for p in positions
            )
        except Exception as e:
            logger.error("Failed to fetch account data: %s", e)
            return

        tasks = []
        sector_pairs = []
        for sector, pairs in self.pairs_by_sector.items():
            for symbols in pairs:
                tasks.append(self.analyze_sector(sector, symbols))
                sector_pairs.append((sector, symbols))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for (sector, symbols), res in zip(sector_pairs, results):
            if isinstance(res, Exception):
                logger.error("Sector %s error: %s", sector, res)
                continue
            if res is None:
                continue
            pair_id = f"{sector}_{res['symbol1']}_{res['symbol2']}"
            if pair_id in self.risk._positions or (pair_id + "_2") in self.risk._positions:
                closed = await self.check_exits_and_trailing(res, pair_id)
                if closed:
                    continue
            if res.get("signal") and not res.get("exit"):
                await self.check_funding_and_execute(res, equity, positions_value)

    async def run(self, interval_sec: int = 300) -> None:
        """Запуск бота с интервалом обновления."""
        try:
            await self.exchange.connect()
            total_pairs = sum(len(pairs) for pairs in self.pairs_by_sector.values())
            logger.info("Bot started. Monitoring %d sectors, %d pairs", len(self.pairs_by_sector), total_pairs)
            while True:
                await self.run_cycle()
                await asyncio.sleep(interval_sec)
        except asyncio.CancelledError:
            pass
        finally:
            await self.exchange.close()


async def main() -> None:
    setup_logging()
    bot = PairsTradingBot()
    try:
        await bot.run(interval_sec=config.RUN_INTERVAL_SEC)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")


if __name__ == "__main__":
    asyncio.run(main())

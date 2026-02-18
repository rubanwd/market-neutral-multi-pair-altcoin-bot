"""
ExchangeHandler — работа с биржей через CCXT (async).
Поддержка Binance/Bybit Futures.
"""
import asyncio
import logging
from typing import Dict, List, Optional, Any
import pandas as pd
import ccxt.async_support as ccxt

import config

logger = logging.getLogger(__name__)

# Маппинг тикеров Bybit: общие имена → актуальные символы (FET/ASI/RENDER делистированы)
BYBIT_SYMBOL_MAP = {
    "MATIC/USDT:USDT": "POL/USDT:USDT",
    "PEPE/USDT:USDT": "1000PEPE/USDT:USDT",
    "MKR/USDT:USDT": "COMP/USDT:USDT",
}


class ExchangeHandler:
    """Асинхронный обработчик биржи через CCXT."""

    def __init__(
        self,
        exchange_id: str = None,
        api_key: str = None,
        api_secret: str = None,
        base_url: Optional[str] = None,
        timeout: int = 10000,
    ):
        self.exchange_id = exchange_id or config.EXCHANGE
        self._dry_run = config.DRY_RUN
        if self._dry_run:
            self.api_key = ""
            self.api_secret = ""
        else:
            self.api_key = api_key or (
                config.BYBIT_API_KEY if self.exchange_id == "bybit" else config.BINANCE_API_KEY
            )
            self.api_secret = api_secret or (
                config.BYBIT_API_SECRET if self.exchange_id == "bybit" else config.BINANCE_API_SECRET
            )
        self.base_url = base_url or config.EXCHANGE_BASE_URL
        self.timeout = timeout
        self._exchange: Optional[ccxt.Exchange] = None

    async def connect(self) -> None:
        """Создаёт и инициализирует CCXT exchange."""
        try:
            exchange_class = getattr(ccxt, self.exchange_id, None)
            if not exchange_class:
                raise ValueError(f"Unknown exchange: {self.exchange_id}")

            options = {
                "defaultType": "future",
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            }
            if self.exchange_id == "bybit":
                options["options"] = {"defaultType": "linear"}

            self._exchange = exchange_class({
                "apiKey": self.api_key,
                "secret": self.api_secret,
                "timeout": self.timeout,
                "options": options,
            })
            # В DRY_RUN используем mainnet для данных (больше пар). В LIVE — testnet/mainnet из конфига.
            if not self._dry_run and self.base_url and ("demo" in self.base_url.lower() or "testnet" in self.base_url.lower()):
                if hasattr(self._exchange, "set_sandbox_mode"):
                    self._exchange.set_sandbox_mode(True)
            await self._exchange.load_markets()
            mode = "DRY_RUN (без ордеров)" if self._dry_run else "LIVE"
            logger.info("Exchange connected: %s [%s]", self.exchange_id, mode)
        except Exception as e:
            logger.error("Exchange connection failed: %s", e)
            raise

    async def close(self) -> None:
        """Закрывает соединение с биржей."""
        if self._exchange:
            await self._exchange.close()
            self._exchange = None
            logger.info("Exchange connection closed")

    def _ensure_connected(self) -> ccxt.Exchange:
        if not self._exchange:
            raise RuntimeError("Exchange not connected. Call connect() first.")
        return self._exchange

    def _resolve_symbol(self, symbol: str) -> str:
        """Преобразует символ (FET→ASI, RNDR→RENDER на Bybit)."""
        if self.exchange_id == "bybit":
            return BYBIT_SYMBOL_MAP.get(symbol, symbol)
        return symbol

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = None,
        limit: int = 200,
    ) -> pd.DataFrame:
        """Получает OHLCV свечи."""
        symbol = self._resolve_symbol(symbol)
        ex = self._ensure_connected()
        tf = timeframe or config.TIMEFRAME
        try:
            ohlcv = await asyncio.wait_for(
                ex.fetch_ohlcv(symbol, tf, limit=limit),
                timeout=self.timeout / 1000 + 5,
            )
        except asyncio.TimeoutError:
            logger.warning("Timeout fetching OHLCV for %s", symbol)
            raise
        except Exception as e:
            logger.error("OHLCV fetch error for %s: %s", symbol, e)
            raise

        df = pd.DataFrame(
            ohlcv,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("timestamp", inplace=True)
        return df

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """Получает тикер."""
        symbol = self._resolve_symbol(symbol)
        ex = self._ensure_connected()
        try:
            return await asyncio.wait_for(ex.fetch_ticker(symbol), timeout=self.timeout / 1000 + 5)
        except Exception as e:
            logger.error("Ticker fetch error for %s: %s", symbol, e)
            raise

    async def fetch_funding_rate(self, symbol: str) -> Optional[float]:
        """Получает ставку финансирования (funding rate)."""
        symbol = self._resolve_symbol(symbol)
        ex = self._ensure_connected()
        try:
            fr = await asyncio.wait_for(ex.fetch_funding_rate(symbol), timeout=self.timeout / 1000 + 5)
            rate = fr.get("fundingRate") or fr.get("funding")
            return float(rate) * 100 if rate is not None else None
        except Exception as e:
            logger.warning("Funding rate fetch error for %s: %s", symbol, e)
            return None

    async def fetch_open_interest(self, symbol: str) -> Optional[float]:
        """Получает Open Interest."""
        symbol = self._resolve_symbol(symbol)
        ex = self._ensure_connected()
        try:
            oi = await asyncio.wait_for(ex.fetch_open_interest(symbol), timeout=self.timeout / 1000 + 5)
            return float(oi.get("openInterestAmount", 0) or oi.get("openInterest", 0))
        except Exception as e:
            logger.warning("Open interest fetch error for %s: %s", symbol, e)
            return None

    async def fetch_balance(self) -> Dict[str, Any]:
        """Получает баланс."""
        if self._dry_run:
            return {"total": {"USDT": config.DRY_RUN_EQUITY}}
        ex = self._ensure_connected()
        try:
            return await asyncio.wait_for(ex.fetch_balance(), timeout=self.timeout / 1000 + 5)
        except Exception as e:
            logger.error("Balance fetch error: %s", e)
            raise

    async def get_equity_usdt(self) -> float:
        """Возвращает эквити в USDT."""
        if self._dry_run:
            return config.DRY_RUN_EQUITY
        bal = await self.fetch_balance()
        total = bal.get("total", {})
        return float(total.get("USDT", 0) or 0)

    async def fetch_positions(self, symbol: Optional[str] = None) -> List[Dict]:
        """Получает открытые позиции."""
        if self._dry_run:
            return []
        ex = self._ensure_connected()
        try:
            positions = await asyncio.wait_for(ex.fetch_positions(symbols=[symbol] if symbol else None), timeout=self.timeout / 1000 + 5)
            return [p for p in positions if float(p.get("contracts", 0) or 0) != 0]
        except Exception as e:
            logger.error("Positions fetch error: %s", e)
            raise

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        """Устанавливает плечо."""
        symbol = self._resolve_symbol(symbol)
        if self._dry_run:
            logger.info("[DRY_RUN] Leverage %s set for %s", leverage, symbol)
            return
        ex = self._ensure_connected()
        try:
            await asyncio.wait_for(ex.set_leverage(leverage, symbol), timeout=self.timeout / 1000 + 5)
            logger.info("Leverage %s set for %s", leverage, symbol)
        except Exception as e:
            logger.error("Set leverage error for %s: %s", symbol, e)
            raise

    async def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        params: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """Создаёт рыночный ордер."""
        symbol = self._resolve_symbol(symbol)
        if self._dry_run:
            logger.info("[DRY_RUN] Order: %s %s %s", side, amount, symbol)
            return {"id": "dry_run", "symbol": symbol, "side": side, "amount": amount}
        ex = self._ensure_connected()
        try:
            order = await asyncio.wait_for(
                ex.create_order(symbol, "market", side, amount, params=params or {}),
                timeout=self.timeout / 1000 + 5,
            )
            logger.info("Order placed: %s %s %s @ %s", side, amount, symbol, order.get("id"))
            return order
        except Exception as e:
            logger.error("Order error %s %s %s: %s", side, amount, symbol, e)
            raise

    async def close_position(self, symbol: str, side: str, amount: float) -> Optional[Dict]:
        """Закрывает позицию рыночным ордером."""
        close_side = "sell" if side == "buy" else "buy"
        return await self.create_market_order(symbol, close_side, amount)

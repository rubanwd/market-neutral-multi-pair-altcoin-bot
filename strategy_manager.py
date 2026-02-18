"""
StrategyManager — логика парного трейдинга (Statistical Arbitrage).
Z-Score, EMA Ribbon, RSI Divergence, Open Interest.
"""
import logging
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
import pandas_ta as ta

import config

logger = logging.getLogger(__name__)


class StrategyManager:
    """Менеджер стратегии парного трейдинга."""

    def __init__(
        self,
        zscore_window: int = None,
        zscore_entry: float = None,
        zscore_exit_tp: float = None,
        zscore_exit_sl: float = None,
        ema_periods: List[int] = None,
    ):
        self.zscore_window = zscore_window or config.ZSCORE_WINDOW
        self.zscore_entry = zscore_entry or config.ZSCORE_ENTRY_THRESHOLD
        self.zscore_exit_tp = zscore_exit_tp or config.ZSCORE_EXIT_TP
        self.zscore_exit_sl = zscore_exit_sl or config.ZSCORE_EXIT_SL
        self.ema_periods = ema_periods or config.EMA_PERIODS

    def calc_spread(self, df1: pd.DataFrame, df2: pd.DataFrame) -> pd.Series:
        """Считает спред (ratio) между двумя активами."""
        close1 = df1["close"].reindex(df2.index).ffill().bfill()
        close2 = df2["close"]
        return close1 / close2

    def calc_zscore(self, series: pd.Series, window: int = None) -> pd.Series:
        """Z-Score спреда: (x - mean) / std."""
        w = window or self.zscore_window
        mean = series.rolling(w).mean()
        std = series.rolling(w).std()
        z = (series - mean) / std
        return z.replace([np.inf, -np.inf], np.nan)

    def get_zscore_signal(self, zscore: float) -> Optional[str]:
        """
        Возвращает сигнал: 'long_short' | 'short_long' | None.
        long_short: Long первый актив (перепродан), Short второй (перекуплен)
        short_long: Short первый, Long второй
        """
        if zscore > self.zscore_entry:
            return "short_long"  # первый перекуплен, второй перепродан
        if zscore < -self.zscore_entry:
            return "long_short"  # первый перепродан, второй перекуплен
        return None

    def check_exit(self, zscore: float) -> Optional[str]:
        """Проверка выхода: 'tp' | 'sl' | None."""
        if abs(zscore - self.zscore_exit_tp) < 0.1:
            return "tp"
        if zscore >= self.zscore_exit_sl or zscore <= -self.zscore_exit_sl:
            return "sl"
        return None

    def add_ema_ribbon(self, df: pd.DataFrame) -> pd.DataFrame:
        """Добавляет EMA Ribbon (20, 50, 100, 200)."""
        out = df.copy()
        for p in self.ema_periods:
            out[f"ema_{p}"] = ta.ema(df["close"], length=p)
        return out

    def ema_filter(self, df: pd.DataFrame, side: str) -> bool:
        """
        Long только если цена выше EMA 50.
        Short только если цена ниже EMA 50.
        """
        if "ema_50" not in df.columns:
            df = self.add_ema_ribbon(df)
        close = df["close"].iloc[-1]
        ema50 = df["ema_50"].iloc[-1]
        if side == "long":
            return close > ema50
        if side == "short":
            return close < ema50
        return False

    def add_rsi(self, df: pd.DataFrame, length: int = 14) -> pd.DataFrame:
        """Добавляет RSI."""
        out = df.copy()
        out["rsi"] = ta.rsi(df["close"], length=length)
        return out

    def detect_rsi_divergence(
        self,
        df: pd.DataFrame,
        lookback: int = 10,
        order: int = 3,
    ) -> Optional[str]:
        """
        Простая проверка RSI дивергенции.
        Возвращает: 'bullish' | 'bearish' | None.
        Bullish: цена делает lower low, RSI — higher low.
        Bearish: цена делает higher high, RSI — lower high.
        """
        if len(df) < lookback * 2:
            return None
        df = self.add_rsi(df)
        if "rsi" not in df.columns or df["rsi"].isna().all():
            return None

        from scipy.signal import argrelextrema

        price = df["close"].values
        rsi = df["rsi"].fillna(50).values

        try:
            lows_p = argrelextrema(price, np.less_equal, order=order)[0]
            lows_r = argrelextrema(rsi, np.less_equal, order=order)[0]
            highs_p = argrelextrema(price, np.greater_equal, order=order)[0]
            highs_r = argrelextrema(rsi, np.greater_equal, order=order)[0]

            if len(lows_p) >= 2 and len(lows_r) >= 2:
                if price[lows_p[-1]] < price[lows_p[-2]] and rsi[lows_r[-1]] > rsi[lows_r[-2]]:
                    return "bullish"
            if len(highs_p) >= 2 and len(highs_r) >= 2:
                if price[highs_p[-1]] > price[highs_p[-2]] and rsi[highs_r[-1]] < rsi[highs_r[-2]]:
                    return "bearish"
        except Exception as e:
            logger.debug("RSI divergence check failed: %s", e)
        return None

    def rsi_divergence_filter(
        self,
        df1: pd.DataFrame,
        df2: pd.DataFrame,
        signal: str,
    ) -> bool:
        """
        Фильтр: входить только при подтверждении дивергенции.
        long_short: нужна bullish на первом (перепроданном)
        short_long: нужна bearish на первом (перекупленном)
        """
        div1 = self.detect_rsi_divergence(df1)
        div2 = self.detect_rsi_divergence(df2)
        if signal == "long_short":
            return div1 == "bullish" or div2 == "bearish"
        if signal == "short_long":
            return div1 == "bearish" or div2 == "bullish"
        return True

    def oi_filter(
        self,
        oi_current: float,
        oi_prev: float,
        price_up: bool,
        side: str,
    ) -> bool:
        """
        OI растёт вместе с движением цены.
        Long: цена вверх, OI вверх — ок.
        Short: цена вниз, OI вверх — ок (short squeeze potential).
        Упрощённо: OI должен расти при нашем направлении.
        """
        if oi_prev is None or oi_prev == 0:
            return True
        oi_up = oi_current > oi_prev
        if side == "long" and price_up and oi_up:
            return True
        if side == "short" and not price_up and oi_up:
            return True
        return oi_up

    def analyze_pair(
        self,
        df1: pd.DataFrame,
        df2: pd.DataFrame,
        symbol1: str,
        symbol2: str,
        oi1: Optional[float] = None,
        oi1_prev: Optional[float] = None,
        oi2: Optional[float] = None,
        oi2_prev: Optional[float] = None,
    ) -> Dict:
        """
        Полный анализ пары. Возвращает:
        {
            "signal": "long_short" | "short_long" | None,
            "zscore": float,
            "ema_ok": bool,
            "rsi_ok": bool,
            "oi_ok": bool,
            "exit": "tp" | "sl" | None,
        }
        """
        df1 = self.add_ema_ribbon(df1)
        df2 = self.add_ema_ribbon(df2)
        spread = self.calc_spread(df1, df2)
        zscore_series = self.calc_zscore(spread)
        zscore = float(zscore_series.iloc[-1]) if len(zscore_series) > 0 else 0.0

        if np.isnan(zscore):
            zscore = 0.0

        signal = self.get_zscore_signal(zscore)
        exit_reason = self.check_exit(zscore)

        ema_ok = True
        rsi_ok = True
        oi_ok = True

        if signal:
            if config.USE_EMA_FILTER:
                if signal == "long_short":
                    ema_ok = self.ema_filter(df1, "long") and self.ema_filter(df2, "short")
                else:
                    ema_ok = self.ema_filter(df1, "short") and self.ema_filter(df2, "long")
            if config.USE_RSI_FILTER:
                rsi_ok = self.rsi_divergence_filter(df1, df2, signal)
            if config.USE_OI_FILTER and oi1 is not None:
                price1_up = df1["close"].iloc[-1] > df1["close"].iloc[-5] if len(df1) >= 5 else True
                oi_ok = self.oi_filter(oi1 or 0, oi1_prev or 0, price1_up, "long" if signal == "long_short" else "short")

        return {
            "signal": signal,
            "zscore": zscore,
            "ema_ok": ema_ok,
            "rsi_ok": rsi_ok,
            "oi_ok": oi_ok,
            "exit": exit_reason,
            "spread": spread.iloc[-1] if len(spread) > 0 else None,
        }

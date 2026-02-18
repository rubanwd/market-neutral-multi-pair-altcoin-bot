"""
RiskManager — управление рисками.
Leverage, position sizing, funding rate, trailing stop, basket risk.
"""
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass

import config

logger = logging.getLogger(__name__)


@dataclass
class PositionState:
    """Состояние позиции для trailing stop."""
    symbol: str
    side: str
    entry_price: float
    entry_zscore: float
    amount: float
    trailing_activated: bool = False
    best_price: float = 0.0


class RiskManager:
    """Менеджер рисков."""

    def __init__(
        self,
        leverage: int = None,
        risk_per_trade_pct: float = None,
        max_basket_risk_pct: float = None,
        max_funding_rate_pct: float = None,
        trailing_stop_pct: float = None,
        zscore_trailing_activation: float = None,
    ):
        self.leverage = leverage or config.LEVERAGE
        self.risk_per_trade_pct = risk_per_trade_pct or config.RISK_PER_TRADE_PCT
        self.max_basket_risk_pct = max_basket_risk_pct or config.MAX_BASKET_RISK_PCT
        self.max_funding_rate_pct = max_funding_rate_pct or config.MAX_FUNDING_RATE_PCT
        self.trailing_stop_pct = trailing_stop_pct or config.TRAILING_STOP_PCT
        self.zscore_trailing_activation = zscore_trailing_activation or config.ZSCORE_TRAILING_ACTIVATION
        self._positions: Dict[str, PositionState] = {}

    def check_leverage(self, leverage: int) -> bool:
        """Плечо не более 5x."""
        return 1 <= leverage <= min(5, self.leverage)

    def calc_position_size(
        self,
        equity: float,
        price: float,
        risk_pct: float = None,
    ) -> float:
        """
        Размер позиции в базовой валюте.
        Риск 1% от эквити на пару (две ноги). На каждую ногу — половина риска.
        Стоп ~5% от входа (Z-Score exit).
        """
        r = (risk_pct or self.risk_per_trade_pct) / 100.0
        risk_per_leg = equity * r / 2
        stop_pct = 0.05
        position_value = risk_per_leg / stop_pct
        size = position_value / price if price > 0 else 0
        return max(0, size)

    def check_basket_risk(
        self,
        equity: float,
        open_positions_value: float,
        new_trade_value: float,
    ) -> bool:
        """Общий риск портфеля не более 5%."""
        total_exposure = open_positions_value + new_trade_value
        exposure_pct = (total_exposure / equity) * 100 if equity > 0 else 0
        return exposure_pct <= self.max_basket_risk_pct

    def check_funding_rate(
        self,
        funding_rate_pct: float,
        side: str,
    ) -> bool:
        """
        Не входить если funding > 0.03% против позиции.
        Long: funding отрицательный — платим, не входим если funding > 0.03%
        Short: funding положительный — платим, не входим если funding > 0.03%
        """
        if funding_rate_pct is None:
            return True
        if side == "long" and funding_rate_pct > self.max_funding_rate_pct:
            return False
        if side == "short" and funding_rate_pct < -self.max_funding_rate_pct:
            return False
        return True

    def register_position(
        self,
        pair_id: str,
        symbol: str,
        side: str,
        entry_price: float,
        entry_zscore: float,
        amount: float,
    ) -> None:
        """Регистрирует открытую позицию для trailing stop."""
        self._positions[pair_id] = PositionState(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            entry_zscore=entry_zscore,
            amount=amount,
        )

    def update_trailing(
        self,
        pair_id: str,
        current_price: float,
        current_zscore: float,
    ) -> Optional[bool]:
        """
        Обновляет trailing stop. Возвращает True если нужно закрыть (stop hit).
        Активация trailing после достижения Z-Score 0.5.
        """
        if pair_id not in self._positions:
            return None
        pos = self._positions[pair_id]
        if abs(current_zscore) <= self.zscore_trailing_activation:
            pos.trailing_activated = True
            if pos.side == "long":
                pos.best_price = max(pos.best_price or 0, current_price)
            else:
                pos.best_price = min(pos.best_price or float("inf"), current_price) if pos.best_price else current_price

        if not pos.trailing_activated:
            return None

        stop_distance = pos.entry_price * (self.trailing_stop_pct / 100)
        if pos.side == "long":
            stop_price = pos.best_price - stop_distance
            if current_price <= stop_price:
                return True
        else:
            stop_price = pos.best_price + stop_distance
            if current_price >= stop_price:
                return True
        return False

    def remove_position(self, pair_id: str) -> None:
        """Удаляет позицию из трекинга."""
        self._positions.pop(pair_id, None)

    def get_open_positions_count(self) -> int:
        return len(self._positions)

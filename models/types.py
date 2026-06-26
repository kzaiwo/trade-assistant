from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class SignalDirection(Enum):
    BUY = "buy"
    SELL = "sell"
    NEUTRAL = "neutral"


@dataclass
class SignalResult:
    direction: SignalDirection
    confidence: float
    weight: float
    reason: str
    signal_name: str
    timeframe: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction.value,
            "confidence": round(float(self.confidence), 6),
            "weight": round(float(self.weight), 6),
            "reason": self.reason,
            "signal_name": self.signal_name,
            "timeframe": self.timeframe,
        }


@dataclass
class MarketContext:
    trend: str
    volatility: str

    def to_dict(self) -> dict[str, str]:
        return {"trend": self.trend, "volatility": self.volatility}


@dataclass
class StrategyResult:
    direction: SignalDirection
    confidence: float
    signal_results: list[SignalResult]
    triggered_rule: str
    market_context: MarketContext | None
    cooldown_active: bool

    def summarize(self) -> str:
        signals = ", ".join(
            f"{s.signal_name}:{s.direction.value}@{s.confidence:.2f}"
            for s in self.signal_results
        )
        context = self.market_context.to_dict() if self.market_context else None
        return (
            f"{self.direction.value.upper()} confidence={self.confidence:.2f}; "
            f"signals=[{signals}]; context={context}; rule={self.triggered_rule}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction.value,
            "confidence": round(float(self.confidence), 6),
            "signal_results": [s.to_dict() for s in self.signal_results],
            "triggered_rule": self.triggered_rule,
            "market_context": self.market_context.to_dict()
            if self.market_context
            else None,
            "cooldown_active": self.cooldown_active,
        }


@dataclass
class TradeResult:
    symbol: str
    entry_time: datetime
    exit_time: datetime
    direction: SignalDirection
    entry_price: float
    exit_price: float
    shares: int
    notional_at_entry: float
    confidence_at_entry: float
    pnl: float
    pnl_pct: float
    strategy_name: str
    signal_results_at_entry: list[SignalResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "entry_time": self.entry_time.isoformat(),
            "exit_time": self.exit_time.isoformat(),
            "direction": self.direction.value,
            "entry_price": round(float(self.entry_price), 6),
            "exit_price": round(float(self.exit_price), 6),
            "shares": int(self.shares),
            "notional_at_entry": round(float(self.notional_at_entry), 6),
            "confidence_at_entry": round(float(self.confidence_at_entry), 6),
            "pnl": round(float(self.pnl), 6),
            "pnl_pct": round(float(self.pnl_pct), 6),
            "strategy_name": self.strategy_name,
            "signal_results_at_entry": [
                s.to_dict() for s in self.signal_results_at_entry
            ],
        }


@dataclass
class Position:
    symbol: str
    direction: SignalDirection
    entry_price: float
    shares: int
    notional_at_entry: float
    entry_time: datetime
    confidence_at_entry: float
    strategy_name: str
    signal_results_at_entry: list[SignalResult] = field(default_factory=list)


class PositionTracker:
    def __init__(self):
        self._positions: dict[str, Position] = {}

    def has_position(self, symbol: str, direction: SignalDirection | None = None) -> bool:
        position = self._positions.get(symbol)
        if position is None:
            return False
        return direction is None or position.direction == direction

    def open_position(self, position: Position):
        self._positions[position.symbol] = position

    def close_position(self, symbol: str) -> Position:
        return self._positions.pop(symbol)

    def get_open_positions(self) -> list[Position]:
        return list(self._positions.values())

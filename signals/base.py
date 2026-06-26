from abc import ABC, abstractmethod

import pandas as pd

from models.types import SignalDirection, SignalResult


class Signal(ABC):
    name: str
    display_name: str
    description: str
    category: str
    required_indicators: list[type] = []
    timeframe: str = "1m"
    weight: float = 1.0
    default_params: dict = {}

    def __init__(self, timeframe: str | None = None, weight: float | None = None, **params):
        if timeframe:
            self.timeframe = timeframe
        if weight is not None:
            self.weight = weight
        self.params = {**self.default_params, **params}

    @abstractmethod
    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        """Return a Series of SignalResult per row."""

    def neutral(self, reason: str = "No signal") -> SignalResult:
        return SignalResult(
            SignalDirection.NEUTRAL,
            0.0,
            self.weight,
            reason,
            self.name,
            self.timeframe,
        )

    def _apply_cooldown(self, results: list[SignalResult]) -> list[SignalResult]:
        cooldown = int(self.params.get("cooldown_bars", 0) or 0)
        if cooldown <= 0:
            return results
        remaining = 0
        cooled: list[SignalResult] = []
        for result in results:
            if remaining > 0 and result.direction != SignalDirection.NEUTRAL:
                cooled.append(self.neutral(f"Cooldown active after {self.name}"))
                remaining -= 1
                continue
            cooled.append(result)
            if result.direction != SignalDirection.NEUTRAL:
                remaining = cooldown
            elif remaining > 0:
                remaining -= 1
        return cooled

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "category": self.category,
            "timeframe": self.timeframe,
            "weight": self.weight,
        }

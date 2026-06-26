from __future__ import annotations

from abc import ABC
from dataclasses import dataclass

import pandas as pd

from models.types import MarketContext, SignalDirection, SignalResult, StrategyResult
from signals.base import Signal


@dataclass
class And:
    conditions: list


@dataclass
class Or:
    conditions: list


@dataclass
class Not:
    condition: object


class Strategy(ABC):
    name: str
    display_name: str
    description: str
    rule: And | Or | Not | Signal
    valid_contexts: list[str] | None = None

    def run(self, bars: dict[str, pd.DataFrame]) -> pd.DataFrame:
        working = {timeframe: df.copy() for timeframe, df in bars.items()}
        signals = self._collect_signals(self.rule)
        primary_timeframe = signals[0].timeframe if signals else "1m"
        for signal in signals:
            df = working[signal.timeframe]
            for indicator_cls in signal.required_indicators:
                df = indicator_cls().compute(df)
            working[signal.timeframe] = df

        signal_outputs = {
            id(signal): signal.evaluate(working[signal.timeframe])
            for signal in signals
        }
        base = working[primary_timeframe].copy()
        base = self._add_market_context_columns(base)
        strategy_results: list[StrategyResult] = []
        for idx in base.index:
            result = self._evaluate_node(self.rule, idx, signal_outputs)
            context = self._market_context(base, idx)
            strategy_results.append(
                StrategyResult(
                    direction=result.direction,
                    confidence=result.confidence,
                    signal_results=result.signal_results,
                    triggered_rule=self._rule_name(self.rule),
                    market_context=context,
                    cooldown_active=False,
                )
            )
        base["strategy_result"] = strategy_results
        base["direction"] = [r.direction for r in strategy_results]
        base["confidence"] = [r.confidence for r in strategy_results]
        return base

    def _collect_signals(self, node) -> list[Signal]:
        if isinstance(node, Signal):
            return [node]
        if isinstance(node, And) or isinstance(node, Or):
            signals: list[Signal] = []
            for condition in node.conditions:
                signals.extend(self._collect_signals(condition))
            return signals
        if isinstance(node, Not):
            return self._collect_signals(node.condition)
        return []

    def _evaluate_node(self, node, idx, signal_outputs) -> StrategyResult:
        if isinstance(node, Signal):
            result = signal_outputs[id(node)].loc[idx]
            return StrategyResult(result.direction, result.confidence, [result], node.name, None, False)
        if isinstance(node, And):
            children = [self._evaluate_node(c, idx, signal_outputs) for c in node.conditions]
            active = [c for c in children if c.direction != SignalDirection.NEUTRAL]
            if len(active) != len(children):
                return StrategyResult(SignalDirection.NEUTRAL, 0.0, self._flatten(children), self._rule_name(node), None, False)
            directions = {c.direction for c in active}
            if len(directions) != 1:
                return StrategyResult(SignalDirection.NEUTRAL, 0.0, self._flatten(children), self._rule_name(node), None, False)
            weights = [max(sum(s.weight for s in c.signal_results), 1e-9) for c in active]
            confidence = sum(c.confidence * w for c, w in zip(active, weights)) / sum(weights)
            return StrategyResult(active[0].direction, confidence, self._flatten(children), self._rule_name(node), None, False)
        if isinstance(node, Or):
            children = [self._evaluate_node(c, idx, signal_outputs) for c in node.conditions]
            active = [c for c in children if c.direction != SignalDirection.NEUTRAL]
            if not active:
                return StrategyResult(SignalDirection.NEUTRAL, 0.0, self._flatten(children), self._rule_name(node), None, False)
            best = max(active, key=lambda c: c.confidence * max(sum(s.weight for s in c.signal_results), 1e-9))
            return StrategyResult(best.direction, best.confidence, self._flatten(children), self._rule_name(node), None, False)
        if isinstance(node, Not):
            child = self._evaluate_node(node.condition, idx, signal_outputs)
            direction = SignalDirection.NEUTRAL if child.direction != SignalDirection.NEUTRAL else SignalDirection.BUY
            return StrategyResult(direction, 1 - child.confidence, child.signal_results, self._rule_name(node), None, False)
        raise TypeError(f"Unsupported rule node: {node}")

    def _flatten(self, results: list[StrategyResult]) -> list[SignalResult]:
        out: list[SignalResult] = []
        for result in results:
            out.extend(result.signal_results)
        return out

    def _rule_name(self, node) -> str:
        if isinstance(node, Signal):
            return node.name
        if isinstance(node, And):
            return " AND ".join(self._rule_name(c) for c in node.conditions)
        if isinstance(node, Or):
            return "(" + " OR ".join(self._rule_name(c) for c in node.conditions) + ")"
        if isinstance(node, Not):
            return f"NOT {self._rule_name(node.condition)}"
        return str(node)

    def _market_context(self, df: pd.DataFrame, idx) -> MarketContext:
        change = df["_context_change"].iat[idx]
        vol = df["_context_vol"].iat[idx]
        if pd.isna(change) or pd.isna(vol):
            return MarketContext("choppy", "normal")
        trend = "ranging" if abs(change) < 0.005 else ("trending" if abs(change) > 0.015 else "choppy")
        volatility = "low" if vol < 0.0015 else ("high" if vol > 0.006 else "normal")
        return MarketContext(trend, volatility)

    def _add_market_context_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["_context_change"] = (df["close"] - df["close"].shift(20)) / df["close"].shift(20)
        df["_context_vol"] = df["close"].pct_change(fill_method=None).rolling(20).std()
        return df

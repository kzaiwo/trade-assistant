from __future__ import annotations

from statistics import mean

from config import BACKTEST_END_DATE, BACKTEST_NOTIONAL, BACKTEST_START_DATE
from models.types import Position, SignalDirection, TradeResult
from runner.base import Runner


class BacktestRunner(Runner):
    def run(self, symbols: list[str]):
        summary = {
            "strategy": self.strategy.name,
            "date_range": f"{BACKTEST_START_DATE} to {BACKTEST_END_DATE}",
            "symbols": symbols,
            "per_symbol": {},
            "overall": {},
        }
        all_trades: list[TradeResult] = []
        for symbol in symbols:
            bars = self.data_source.get_bars(symbol)
            results = self.strategy.run(bars)
            position: Position | None = None
            trades: list[TradeResult] = []
            for row in results.itertuples():
                result = row.strategy_result
                if result.direction != SignalDirection.NEUTRAL and self.journal:
                    self.journal.log_signal(symbol, result, row.time_key, row.close, self.strategy.name)
                if result.direction == SignalDirection.NEUTRAL:
                    continue
                if position is None:
                    shares = self._shares_for_notional(float(row.close))
                    position = Position(
                        symbol=symbol,
                        direction=result.direction,
                        entry_price=float(row.close),
                        shares=shares,
                        notional_at_entry=shares * float(row.close),
                        entry_time=row.time_key.to_pydatetime(),
                        confidence_at_entry=float(result.confidence),
                        strategy_name=self.strategy.name,
                        signal_results_at_entry=result.signal_results,
                    )
                    for notifier in self.notifiers:
                        notifier.send(result, symbol)
                    continue
                if result.direction != position.direction:
                    pnl = self._pnl(position.direction, position.entry_price, float(row.close), position.shares)
                    trade = TradeResult(
                        symbol=symbol,
                        entry_time=position.entry_time,
                        exit_time=row.time_key.to_pydatetime(),
                        direction=position.direction,
                        entry_price=position.entry_price,
                        exit_price=float(row.close),
                        shares=position.shares,
                        notional_at_entry=position.notional_at_entry,
                        confidence_at_entry=position.confidence_at_entry,
                        pnl=pnl,
                        pnl_pct=pnl / position.notional_at_entry * 100 if position.notional_at_entry else 0.0,
                        strategy_name=self.strategy.name,
                        signal_results_at_entry=position.signal_results_at_entry,
                    )
                    trades.append(trade)
                    all_trades.append(trade)
                    if self.journal:
                        self.journal.log_trade(trade)
                    shares = self._shares_for_notional(float(row.close))
                    position = Position(
                        symbol=symbol,
                        direction=result.direction,
                        entry_price=float(row.close),
                        shares=shares,
                        notional_at_entry=shares * float(row.close),
                        entry_time=row.time_key.to_pydatetime(),
                        confidence_at_entry=float(result.confidence),
                        strategy_name=self.strategy.name,
                        signal_results_at_entry=result.signal_results,
                    )
            summary["per_symbol"][symbol] = self._summarize_trades(trades)
        summary["overall"] = self._summarize_overall(all_trades)
        return summary

    def _shares_for_notional(self, entry_price: float) -> int:
        return int(BACKTEST_NOTIONAL // entry_price)

    def _pnl(self, direction: SignalDirection, entry: float, exit_price: float, shares: int) -> float:
        if direction == SignalDirection.BUY:
            return (exit_price - entry) * shares
        return (entry - exit_price) * shares

    def _summarize_trades(self, trades: list[TradeResult]) -> dict:
        wins = sum(1 for t in trades if t.pnl > 0)
        losses = sum(1 for t in trades if t.pnl <= 0)
        return {
            "total_trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / len(trades), 3) if trades else 0.0,
            "total_pnl": round(sum(t.pnl for t in trades), 6),
            "total_pnl_pct": round(sum(t.pnl_pct for t in trades), 6),
            "avg_confidence": round(mean([t.confidence_at_entry for t in trades]), 6) if trades else 0.0,
            "avg_shares": round(mean([t.shares for t in trades]), 2) if trades else 0.0,
            "notional_per_trade": BACKTEST_NOTIONAL,
            "trades": [t.to_dict() for t in trades],
        }

    def _summarize_overall(self, trades: list[TradeResult]) -> dict:
        wins = sum(1 for t in trades if t.pnl > 0)
        losses = sum(1 for t in trades if t.pnl <= 0)
        return {
            "total_trades": len(trades),
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / len(trades), 3) if trades else 0.0,
            "total_pnl": round(sum(t.pnl for t in trades), 6),
            "total_pnl_pct": round(sum(t.pnl_pct for t in trades), 6),
            "notional_per_trade": BACKTEST_NOTIONAL,
        }

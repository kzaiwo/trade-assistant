from __future__ import annotations

import json
from pathlib import Path

from models.types import StrategyResult, TradeResult


class TradeJournal:
    def __init__(self, path: str = "journal"):
        self.path = Path(path)
        self.path.mkdir(parents=True, exist_ok=True)

    def log_signal(self, symbol: str, result: StrategyResult, timestamp=None, price_at_signal=None, strategy: str | None = None):
        entry = {
            "timestamp": timestamp.isoformat() if timestamp is not None else None,
            "symbol": symbol,
            "strategy": strategy,
            "direction": result.direction.value,
            "confidence": round(float(result.confidence), 6),
            "signal_results": [signal.to_dict() for signal in result.signal_results],
            "market_context": result.market_context.to_dict() if result.market_context else None,
            "price_at_signal": round(float(price_at_signal), 6) if price_at_signal is not None else None,
            "cooldown_active": result.cooldown_active,
        }
        self._append(symbol, "signals", entry)

    def log_trade(self, trade: TradeResult):
        self._append(trade.symbol, "trades", trade.to_dict())

    def get_history(self, symbol: str | None = None, last_n: int | None = None) -> list[dict]:
        files = [self.path / symbol / "trades.jsonl"] if symbol else self.path.glob("*/trades.jsonl")
        rows: list[dict] = []
        for file_path in files:
            if file_path.exists():
                rows.extend(json.loads(line) for line in file_path.read_text().splitlines() if line)
        return rows[-last_n:] if last_n else rows

    def summarize(self, symbol: str | None = None) -> str:
        rows = self.get_history(symbol)
        pnl = sum(row.get("pnl", 0) for row in rows)
        wins = sum(1 for row in rows if row.get("pnl", 0) > 0)
        return f"{len(rows)} trades, {wins} wins, total P&L {pnl:.2f}"

    def _append(self, symbol: str, kind: str, payload: dict):
        directory = self.path / symbol
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / f"{kind}.jsonl").open("a") as fh:
            fh.write(json.dumps(payload) + "\n")

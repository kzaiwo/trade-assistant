from __future__ import annotations

import json
from pathlib import Path

from config import BACKTEST_END_DATE, BACKTEST_NOTIONAL, BACKTEST_START_DATE, DATA_BASE_PATH, RESULTS_JSON, RESULTS_TXT, SYMBOLS
from data.file_loader import FileLoader
from journal.logger import TradeJournal
from runner.backtest import BacktestRunner
from signals.bb_squeeze import BBSqueeze
from signals.bb_mid_cross import BBMidCross
from signals.macd_cross import MACDCross
from signals.stoch_cross import StochCross
from signals.vwap_bounce import VWAPBounce
from strategies.bb_mid_cross import BBMidCrossStrategy, BBMidNoChoppyExitEarlyCloseStrategy, BBMidNoChoppyExitStrategy, BBMidNoChoppyStrategy
from strategies.mean_reversion import MeanReversion, SignalStrategy


def build_strategies(names: list[str] | None = None, timeframes: list[str] | None = None):
    timeframes = timeframes or ["1m", "5m"]
    available = {
        f"bb_squeeze_{timeframe}": SignalStrategy(BBSqueeze(timeframe=timeframe))
        for timeframe in timeframes
    }
    for timeframe in timeframes:
        available[f"stoch_cross_{timeframe}"] = SignalStrategy(StochCross(timeframe=timeframe))
        available[f"vwap_bounce_{timeframe}"] = SignalStrategy(VWAPBounce(timeframe=timeframe))
        available[f"macd_cross_{timeframe}"] = SignalStrategy(MACDCross(timeframe=timeframe))
        available[f"bb_mid_cross_{timeframe}"] = BBMidCrossStrategy(timeframe=timeframe)
        available[f"bb_mid_2_no_choppy_{timeframe}"] = BBMidNoChoppyStrategy(timeframe=timeframe)
        available[f"bb_mid_no_choppy_exit_{timeframe}"] = BBMidNoChoppyExitStrategy(timeframe=timeframe)
        available[f"bb_mid_no_choppy_exit_early_close_{timeframe}"] = BBMidNoChoppyExitEarlyCloseStrategy(timeframe=timeframe)
        available[f"mean_reversion_{timeframe}"] = MeanReversion(timeframe=timeframe)
    if names:
        return [available[name] for name in names]
    ordered = []
    for timeframe in timeframes:
        ordered.extend(
            [
                available[f"bb_squeeze_{timeframe}"],
                available[f"stoch_cross_{timeframe}"],
                available[f"vwap_bounce_{timeframe}"],
                available[f"macd_cross_{timeframe}"],
                available[f"bb_mid_cross_{timeframe}"],
                available[f"bb_mid_2_no_choppy_{timeframe}"],
                available[f"bb_mid_no_choppy_exit_{timeframe}"],
                available[f"bb_mid_no_choppy_exit_early_close_{timeframe}"],
                available[f"mean_reversion_{timeframe}"],
            ]
        )
    return ordered


def append_json_summary(run_summary: dict):
    path = Path(RESULTS_JSON)
    path.parent.mkdir(parents=True, exist_ok=True)
    runs = []
    if path.exists():
        runs = json.loads(path.read_text())
    runs = [
        run for run in runs
        if not (
            run.get("strategy") == run_summary.get("strategy")
            and run.get("date_range") == run_summary.get("date_range")
        )
    ]
    runs.append(run_summary)
    path.write_text(json.dumps(runs, indent=2))


def write_text_summary():
    path = Path(RESULTS_JSON)
    if not path.exists():
        return
    runs = json.loads(path.read_text())
    lines = [
        "Backtest Summary",
        f"Date range: {BACKTEST_START_DATE} to {BACKTEST_END_DATE}",
        f"P&L basis: whole shares purchasable with ${BACKTEST_NOTIONAL:,.0f} per trade",
        "",
    ]
    for run in runs:
        overall = run["overall"]
        lines.append(
            f"{run['strategy']}: trades={overall['total_trades']} wins={overall['wins']} "
            f"losses={overall['losses']} win_rate={overall['win_rate'] * 100:.1f}% "
            f"pnl=${overall['total_pnl']:.2f} pnl_pct={overall['total_pnl_pct']:.4f}"
        )
        for symbol, data in run["per_symbol"].items():
            lines.append(
                f"  {symbol:5} trades={data['total_trades']:3} win_rate={data['win_rate'] * 100:.1f}% "
                f"pnl={data['total_pnl']:.2f} avg_shares={data.get('avg_shares', 0):.1f} "
                f"avg_conf={data['avg_confidence']:.3f}"
            )
        lines.append("")

    ranked = sorted(
        runs,
        key=lambda run: (run["overall"]["win_rate"], run["overall"]["total_pnl"]),
        reverse=True,
    )
    lines.extend(
        [
            "Comparison Table",
            f"{'Rank':<4} {'Strategy':<16} {'Trades':>7} {'Win Rate':>8} {'P&L':>12} {'P&L %':>12}",
        ]
    )
    for rank, run in enumerate(ranked, 1):
        overall = run["overall"]
        lines.append(
            f"{rank:<4} {run['strategy']:<16} {overall['total_trades']:>7} "
            f"{overall['win_rate'] * 100:>7.1f}% {overall['total_pnl']:>12.2f} {overall['total_pnl_pct']:>12.4f}"
        )
    Path(RESULTS_TXT).write_text("\n".join(lines) + "\n")


def run_backtests(
    strategy_names: list[str] | None = None,
    timeframes: list[str] | None = None,
    log_journal: bool = False,
):
    Path(RESULTS_JSON).parent.mkdir(parents=True, exist_ok=True)
    data_source = FileLoader(DATA_BASE_PATH, BACKTEST_START_DATE, BACKTEST_END_DATE)
    journal = TradeJournal() if log_journal else None
    summaries = []
    for strategy in build_strategies(strategy_names, timeframes=timeframes):
        runner = BacktestRunner(data_source, strategy, journal=journal)
        summary = runner.run(SYMBOLS)
        append_json_summary(summary)
        write_text_summary()
        summaries.append(summary)
        print(
            f"{strategy.name}: trades={summary['overall']['total_trades']} "
            f"win_rate={summary['overall']['win_rate'] * 100:.1f}% pnl=${summary['overall']['total_pnl']:.2f}"
        )
    return summaries


if __name__ == "__main__":
    run_backtests()

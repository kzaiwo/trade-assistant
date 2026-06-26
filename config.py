SYMBOLS = ["AAPL", "TSLA", "MU", "INTC"]
DATA_BASE_PATH = "../_trade_data"
BACKTEST_START_DATE = "2026-06-01"
BACKTEST_END_DATE = "2026-06-30"
RESULTS_JSON = "results/backtest_summary.json"
RESULTS_TXT = "results/backtest_summary.txt"
BACKTEST_NOTIONAL = 100_000
INCLUDE_EXTENDED_HOURS = False
MARKET_OPEN_TIME = "09:30"
MARKET_CLOSE_TIME = "16:00"

PRESETS = {
    "conservative": {"bb_period": 20, "stoch_threshold": 15, "cooldown_bars": 10},
    "aggressive": {"bb_period": 14, "stoch_threshold": 25, "cooldown_bars": 5},
}

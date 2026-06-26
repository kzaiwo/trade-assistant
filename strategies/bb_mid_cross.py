from signals.bb_mid_cross import BBMidCross
from signals.chop_filter import ChopFilter
from strategies.base import And, Not, Strategy


class BBMidCrossStrategy(Strategy):
    name = "bb_mid_cross"
    display_name = "Bollinger Midline Cross"
    description = "Enters on BB midline cross with slope confirmation, filtered by anti-chop detection."
    strategy_notes = BBMidCross.strategy_notes

    def __init__(self, timeframe: str = "1m"):
        self.timeframe = timeframe
        self.name = f"bb_mid_cross_{timeframe}"
        self.display_name = f"Bollinger Midline Cross ({timeframe})"
        self.rule = And(
            [
                BBMidCross(timeframe=timeframe),
                Not(ChopFilter(timeframe=timeframe)),
            ]
        )
        self.valid_contexts = None


class BBMidNoChoppyStrategy(BBMidCrossStrategy):
    name = "bb_mid_2_no_choppy"
    display_name = "Bollinger Midline Cross No Choppy"
    description = "Same BB midline strategy, but skips fresh entries when BB mid is flat."
    flat_entry_tolerances = {
        "1m": 0.0003,
        "3m": 0.0005,
        "5m": 0.0005,
        "15m": 0.0008,
        "30m": 0.0008,
        "60m": 0.001,
    }
    strategy_notes = [
        "Entry rules: uses the same BB midline signals as Bollinger Midline Cross.",
        "Fresh-entry filter: if BB mid slope is flat for the selected timeframe, do not open a new position.",
        "Exit rules: existing positions may still close on opposite BB midline signals even when BB mid is flat.",
        "Flat thresholds: 1m 0.03%, 3m/5m 0.05%, 15m/30m 0.08%, 60m 0.10% of close per candle.",
        "Best conditions: helps compare whether skipping flat-mid entries reduces whipsaw on choppy days.",
        "Weaknesses: may miss profitable early turns that begin while BB mid is still flattening.",
    ]

    def __init__(self, timeframe: str = "1m"):
        super().__init__(timeframe=timeframe)
        self.name = f"bb_mid_2_no_choppy_{timeframe}"
        self.display_name = f"Bollinger Midline Cross No Choppy ({timeframe})"
        self.description = self.__class__.description
        self.strategy_notes = self.__class__.strategy_notes

    def allows_entry(self, row, _result) -> bool:
        slope = getattr(row, "bb_mid_slope", None)
        close = getattr(row, "close", None)
        if slope is None or close in (None, 0):
            return True
        tolerance = self.flat_entry_tolerances.get(self.timeframe, 0.0005)
        return abs(float(slope)) / abs(float(close)) > tolerance


class BBMidNoChoppyExitStrategy(BBMidNoChoppyStrategy):
    name = "bb_mid_no_choppy_exit"
    display_name = "Bollinger Midline Cross No Choppy Exit"
    description = "Same BB midline strategy, but skips fresh entries and opposite exits when BB mid is flat."
    strategy_notes = [
        "Entry rules: uses the same BB midline signals as Bollinger Midline Cross.",
        "Fresh-entry filter: if BB mid slope is flat for the selected timeframe, do not open a new position.",
        "Exit filter: if BB mid slope is flat, ignore opposite signals and keep holding the current position.",
        "Flat thresholds: 1m 0.03%, 3m/5m 0.05%, 15m/30m 0.08%, 60m 0.10% of close per candle.",
        "Best conditions: tests whether holding through flat-mid noise improves trend capture.",
        "Weaknesses: may hold losers longer when the flat period is a real reversal forming.",
    ]

    def __init__(self, timeframe: str = "1m"):
        super().__init__(timeframe=timeframe)
        self.name = f"bb_mid_no_choppy_exit_{timeframe}"
        self.display_name = f"Bollinger Midline Cross No Choppy Exit ({timeframe})"
        self.description = self.__class__.description
        self.strategy_notes = self.__class__.strategy_notes

    def allows_exit(self, row, result) -> bool:
        return self.allows_entry(row, result)


class BBMidNoChoppyExitEarlyCloseStrategy(BBMidNoChoppyExitStrategy):
    name = "bb_mid_no_choppy_exit_early_close"
    display_name = "Bollinger Midline Cross No Choppy Exit Early Close"
    description = "Same no-choppy-exit strategy, but closes open positions 15 minutes before session end."
    strategy_notes = [
        "Entry rules: uses the same BB midline signals as Bollinger Midline Cross.",
        "Fresh-entry filter: if BB mid slope is flat for the selected timeframe, do not open a new position.",
        "Exit filter: if BB mid slope is flat, ignore opposite signals and keep holding the current position.",
        "Time exit: close any open position 15 minutes before the selected session ends.",
        "Flat thresholds: 1m 0.03%, 3m/5m 0.05%, 15m/30m 0.08%, 60m 0.10% of close per candle.",
        "Best conditions: compares whether avoiding the final 15 minutes reduces close-driven noise.",
        "Weaknesses: can miss late trend continuation into the close.",
    ]

    def __init__(self, timeframe: str = "1m"):
        super().__init__(timeframe=timeframe)
        self.name = f"bb_mid_no_choppy_exit_early_close_{timeframe}"
        self.display_name = f"Bollinger Midline Cross No Choppy Exit Early Close ({timeframe})"
        self.description = self.__class__.description
        self.strategy_notes = self.__class__.strategy_notes

    def allows_entry(self, row, result) -> bool:
        return not self.force_exit(row) and super().allows_entry(row, result)

    def force_exit(self, row) -> bool:
        time_key = getattr(row, "time_key", None)
        if time_key is None:
            return False
        minute = int(time_key.hour) * 60 + int(time_key.minute)
        return minute >= (16 * 60 - 15)

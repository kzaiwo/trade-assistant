from signals.bb_mid_cross import BB_MID_FLAT_TOLERANCES, BBMidChangeDir, BBMidCross, BBMidTrendRider, is_bb_mid_flat
from strategies.base import Strategy


class BBMidCrossStrategy(Strategy):
    name = "bb_mid_cross"
    display_name = "Bollinger Midline Cross"
    description = "Enters on BB midline direction/cross signals with slope confirmation."
    strategy_notes = BBMidCross.strategy_notes
    first_entry_minute = 9 * 60 + 45
    last_entry_minute = 15 * 60 + 10
    reverse_on_opposite = False

    def __init__(self, timeframe: str = "1m"):
        self.timeframe = timeframe
        self.name = f"bb_mid_cross_{timeframe}"
        self.display_name = f"Bollinger Midline Cross ({timeframe})"
        self.rule = BBMidCross(timeframe=timeframe)
        self.valid_contexts = None

    def allows_entry(self, row, _result) -> bool:
        minute = self._minute_of_day(row)
        if minute is None:
            return True
        return self.first_entry_minute <= minute <= self.last_entry_minute

    def _minute_of_day(self, row) -> int | None:
        time_key = getattr(row, "time_key", None)
        if time_key is None:
            return None
        return int(time_key.hour) * 60 + int(time_key.minute)


class BBMidChangeDirStrategy(Strategy):
    name = "bb_mid_change_dir"
    display_name = "Bollinger Midline Change Direction"
    description = "Enters and reverses when BB mid slope changes direction, without requiring a candle cross."
    strategy_notes = BBMidChangeDir.strategy_notes

    def __init__(self, timeframe: str = "1m"):
        self.timeframe = timeframe
        self.name = f"bb_mid_change_dir_{timeframe}"
        self.display_name = f"Bollinger Midline Change Direction ({timeframe})"
        self.rule = BBMidChangeDir(timeframe=timeframe)
        self.valid_contexts = None

    def allows_entry(self, row, result) -> bool:
        return BBMidCrossStrategy.allows_entry(self, row, result)

    def _minute_of_day(self, row) -> int | None:
        return BBMidCrossStrategy._minute_of_day(self, row)


class BBMidTrendRiderStrategy(BBMidCrossStrategy):
    name = "bb_mid_trend_rider"
    display_name = "Bollinger Midline Trend Rider"
    description = "Rides directional moves while price holds the trend side of a non-flat BB midline."
    strategy_notes = BBMidTrendRider.strategy_notes

    def __init__(self, timeframe: str = "1m"):
        super().__init__(timeframe=timeframe)
        self.name = f"bb_mid_trend_rider_{timeframe}"
        self.display_name = f"Bollinger Midline Trend Rider ({timeframe})"
        self.description = self.__class__.description
        self.rule = BBMidTrendRider(timeframe=timeframe)
        self.strategy_notes = self.__class__.strategy_notes


class BBMidNoChoppyStrategy(BBMidCrossStrategy):
    name = "bb_mid_2_no_choppy"
    display_name = "Bollinger Midline Cross No Choppy"
    description = "Same BB midline strategy, but skips fresh entries when BB mid is flat."
    flat_entry_tolerances = BB_MID_FLAT_TOLERANCES
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
        if not super().allows_entry(row, _result):
            return False
        return not self._flat_entry(row)

    def _flat_entry(self, row) -> bool:
        return is_bb_mid_flat(row, self.timeframe)


class BBMidWidthCheckStrategy(BBMidCrossStrategy):
    name = "bb_mid_width_check"
    display_name = "Bollinger Midline Width Check"
    description = "Same BB midline strategy, but blocks fresh entries when BB mid is flat and Bollinger bandwidth is tight without expansion."
    width_ratio_thresholds = {
        "1m": 0.007,
        "3m": 0.008,
        "5m": 0.008,
        "15m": 0.010,
        "30m": 0.012,
        "60m": 0.014,
    }
    strategy_notes = [
        "Entry rules: uses the same BB midline signals as Bollinger Midline Cross.",
        "Fresh-entry filter: block a new position only when BB mid is flat and Bollinger bandwidth is narrow without expanding.",
        "Exit rules: existing positions may still close on opposite BB midline signals even when width is tight.",
        "Width filter: uses BB width percent, calculated as (upper band - lower band) / BB mid.",
        "Best conditions: helps avoid midline-cross churn inside tight squeezes.",
        "Weaknesses: can skip early entries that start from a squeeze before the bands visibly expand.",
    ]

    def __init__(self, timeframe: str = "1m"):
        super().__init__(timeframe=timeframe)
        self.name = f"bb_mid_width_check_{timeframe}"
        self.display_name = f"Bollinger Midline Width Check ({timeframe})"
        self.description = self.__class__.description
        self.strategy_notes = self.__class__.strategy_notes

    def allows_entry(self, row, _result) -> bool:
        if not super().allows_entry(row, _result):
            return False
        slope = getattr(row, "bb_mid_slope", None)
        close = getattr(row, "close", None)
        width = getattr(row, "bb_bandwidth", None)
        expanding = bool(getattr(row, "bb_bandwidth_expanding", False))
        if slope is None or close in (None, 0) or width is None:
            return True
        flat_tolerance = BBMidNoChoppyStrategy.flat_entry_tolerances.get(self.timeframe, 0.0005)
        flat_mid = abs(float(slope)) / abs(float(close)) <= flat_tolerance
        width_threshold = self.width_ratio_thresholds.get(self.timeframe, 0.008)
        narrow_width = float(width) <= width_threshold
        return not (flat_mid and narrow_width and not expanding)


class BBMidCrossChopGuardStrategy(BBMidCrossStrategy):
    name = "bb_mid_cross_chop_guard"
    display_name = "Bollinger Midline Cross Chop Guard"
    description = "Same BB midline strategy, but blocks fresh entries when recent BB mid movement is inefficient."
    chop_settings = {
        "1m": {"lookback": 8, "min_net_move": 0.0006, "min_efficiency": 0.35},
        "3m": {"lookback": 5, "min_net_move": 0.0008, "min_efficiency": 0.35},
        "5m": {"lookback": 4, "min_net_move": 0.0010, "min_efficiency": 0.35},
        "15m": {"lookback": 3, "min_net_move": 0.0015, "min_efficiency": 0.35},
        "30m": {"lookback": 3, "min_net_move": 0.0018, "min_efficiency": 0.35},
        "60m": {"lookback": 3, "min_net_move": 0.0020, "min_efficiency": 0.35},
    }
    strategy_notes = [
        "Entry rules: uses the same BB midline signals as Bollinger Midline Cross.",
        "Fresh-entry filter: blocks new positions when recent BB mid movement has low net travel or low efficiency.",
        "Exit rules: existing positions may still close on opposite BB midline signals even when the chop guard is active.",
        "Chop check: compares net BB mid movement over the lookback to total absolute BB mid movement.",
        "Best conditions: helps avoid repeated entries when BB mid wiggles without making progress.",
        "Weaknesses: may skip early turns that start after a compressed or back-and-forth midline.",
    ]

    def __init__(self, timeframe: str = "1m"):
        super().__init__(timeframe=timeframe)
        self.name = f"bb_mid_cross_chop_guard_{timeframe}"
        self.display_name = f"Bollinger Midline Cross Chop Guard ({timeframe})"
        self.description = self.__class__.description
        self.strategy_notes = self.__class__.strategy_notes

    def allows_entry(self, row, result) -> bool:
        if not super().allows_entry(row, result):
            return False
        return not bool(getattr(row, "bb_mid_chop_guard", False))


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
        return not self._flat_entry(row)


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

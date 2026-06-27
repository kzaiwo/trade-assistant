import pandas as pd

from indicators.bollinger_bands import BollingerBands
from models.types import SignalDirection, SignalResult
from signals.base import Signal
from signals.registry import register

BB_MID_FLAT_TOLERANCES = {
    "1m": 0.0003,
    "3m": 0.0005,
    "5m": 0.0005,
    "15m": 0.0008,
    "30m": 0.0008,
    "60m": 0.001,
}
BB_MID_CHOP_GUARD_SETTINGS = {
    "1m": {"lookback": 8, "min_net_move": 0.0006, "min_efficiency": 0.35},
    "3m": {"lookback": 5, "min_net_move": 0.0008, "min_efficiency": 0.35},
    "5m": {"lookback": 4, "min_net_move": 0.0010, "min_efficiency": 0.35},
    "15m": {"lookback": 3, "min_net_move": 0.0015, "min_efficiency": 0.35},
    "30m": {"lookback": 3, "min_net_move": 0.0018, "min_efficiency": 0.35},
    "60m": {"lookback": 3, "min_net_move": 0.0020, "min_efficiency": 0.35},
}


def bb_mid_flat_tolerance(timeframe: str) -> float:
    return BB_MID_FLAT_TOLERANCES.get(str(timeframe or "1m"), 0.0005)


def is_bb_mid_flat(row, timeframe: str) -> bool:
    slope = row.get("bb_mid_slope") if isinstance(row, dict) else getattr(row, "bb_mid_slope", None)
    close = row.get("close") if isinstance(row, dict) else getattr(row, "close", None)
    if pd.isna(slope) or pd.isna(close) or not close:
        return False
    return abs(float(slope)) / abs(float(close)) <= bb_mid_flat_tolerance(timeframe)


def bb_mid_chop_guard_blocked(records: list[dict], pos: int, timeframe: str) -> bool:
    settings = BB_MID_CHOP_GUARD_SETTINGS.get(str(timeframe or "1m"), BB_MID_CHOP_GUARD_SETTINGS["3m"])
    lookback = int(settings["lookback"])
    if pos < lookback:
        return False
    window = records[pos - lookback:pos + 1]
    mids = [row.get("bb_mid") for row in window]
    close = records[pos].get("close")
    if any(pd.isna(mid) for mid in mids) or pd.isna(close) or not close:
        return False
    net_move = abs(float(mids[-1]) - float(mids[0])) / abs(float(close))
    total_move = sum(abs(float(curr) - float(prev)) for prev, curr in zip(mids, mids[1:]))
    efficiency = abs(float(mids[-1]) - float(mids[0])) / total_move if total_move else 0.0
    return net_move < float(settings["min_net_move"]) or efficiency < float(settings["min_efficiency"])


@register
class BBMidCross(Signal):
    name = "bb_mid_cross"
    display_name = "Bollinger Midline Cross"
    description = "Detects price crossing the BB middle line with directional confirmation from BB mid slope."
    category = "mean_reversion"
    required_indicators = [BollingerBands]
    default_params = {"body_threshold": 0.5, "cooldown_bars": 0}
    cooldown_bars = 0
    strategy_notes = [
        "Entry rules: when no position is open, enter long if the close is above BB mid and BB mid is sloping up; enter short if the close is below BB mid and BB mid is sloping down.",
        "Entry rules: when a position is open, close and reverse on the opposite BB mid cross with slope confirmation.",
        "Filters: do not open a new position before 09:45.",
        "Exit rules: the runner closes and reverses when an opposite BB midline cross signal appears.",
        "Filters: skip small doji-like higher-timeframe candles and wait when BB mid is flat on the current candle.",
        "Filters: if BB mid is flat on the current candle, wait for the next candle.",
        "Filters: no cooldown is applied; every valid opposite cross can close and reverse.",
        "Best conditions: works best when price is rotating through BB mid as a new directional move starts.",
        "Weaknesses: can still whipsaw near the midline if slope changes are tiny and volatility has not expanded.",
    ]

    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        self.params["cooldown_bars"] = 0
        body_threshold = float(self.params["body_threshold"])
        results: list[SignalResult] = []
        records = df.to_dict("records")
        df["bb_mid_chop_guard"] = [
            bb_mid_chop_guard_blocked(records, pos, self.timeframe)
            for pos in range(len(records))
        ]
        for pos, row in enumerate(records):
            if pos == 0:
                results.append(self.neutral("Previous candle unavailable"))
                continue
            prev = records[pos - 1]
            if pd.isna(row["bb_mid"]) or pd.isna(prev["bb_mid"]) or pd.isna(row["bb_mid_slope"]):
                results.append(self.neutral("Bollinger midline unavailable"))
                continue
            if self.timeframe == "1m":
                signal = self._one_minute_signal(row, prev)
                if signal.direction == SignalDirection.NEUTRAL:
                    signal = self._cold_start_signal(records, pos)
                results.append(signal)
                continue
            signal = self._higher_timeframe_signal(records, pos, body_threshold)
            if signal.direction == SignalDirection.NEUTRAL:
                signal = self._cold_start_signal(records, pos)
            results.append(signal)
        return pd.Series(self._apply_cooldown(results), index=df.index)

    def _one_minute_signal(self, row, prev) -> SignalResult:
        slope = row["bb_mid_slope"]
        prev_slope = prev["bb_mid_slope"]
        turned_up = slope > 0 and (pd.isna(prev_slope) or prev_slope <= 0)
        turned_down = slope < 0 and (pd.isna(prev_slope) or prev_slope >= 0)
        if prev["close"] < prev["bb_mid"] and row["close"] > row["bb_mid"] and turned_up:
            return SignalResult(SignalDirection.BUY, self._confidence(row, 1), self.weight, "Full candle close crossed above BB mid while BB mid slope turned upward", self.name, self.timeframe)
        if prev["close"] > prev["bb_mid"] and row["close"] < row["bb_mid"] and turned_down:
            return SignalResult(SignalDirection.SELL, self._confidence(row, -1), self.weight, "Full candle close crossed below BB mid while BB mid slope turned downward", self.name, self.timeframe)
        return self.neutral("No BB midline close cross with slope turn")

    def _higher_timeframe_signal(self, records: list[dict], pos: int, body_threshold: float) -> SignalResult:
        row = records[pos]
        prev = records[pos - 1]
        body = abs(row["close"] - row["open"])
        atr = row.get("atr14")
        prev_above, prev_below = self._body_side_ratios(prev)
        above, below = self._body_side_ratios(row)
        slope = row["bb_mid_slope"]
        prev_slope = prev.get("bb_mid_slope")
        flat = is_bb_mid_flat(row, self.timeframe)
        crossed_up = prev["close"] < prev["bb_mid"] and row["close"] > row["bb_mid"]
        crossed_down = prev["close"] > prev["bb_mid"] and row["close"] < row["bb_mid"]
        flat_limit = row["close"] * bb_mid_flat_tolerance(self.timeframe)
        tight_flat = abs(slope) <= flat_limit * 0.1
        prev_flat = pd.notna(prev_slope) and abs(prev_slope) <= flat_limit
        prev_falling = pd.notna(prev_slope) and prev_slope < -flat_limit
        near_mid = abs(row["close"] - row["bb_mid"]) <= flat_limit
        down_turn_limit = flat_limit * 0.37
        turn_threshold = flat_limit * 0.1
        if pd.notna(prev_slope) and prev_slope <= turn_threshold and slope >= turn_threshold and row["close"] > row["bb_mid"] and above >= 0.8:
            return SignalResult(SignalDirection.BUY, self._confidence(row, 1), self.weight, "BB mid turned upward while price held above the midline", self.name, self.timeframe)
        if pd.notna(prev_slope) and prev_slope >= -turn_threshold and slope <= -turn_threshold and row["close"] < row["bb_mid"] and below >= 0.8:
            return SignalResult(SignalDirection.SELL, self._confidence(row, -1), self.weight, "BB mid turned downward while price held below the midline", self.name, self.timeframe)
        if (prev_falling and flat and near_mid) or (prev_flat and prev_slope <= 0 and slope > 0 and above >= 0.8):
            return SignalResult(SignalDirection.BUY, self._confidence(row, 1), self.weight, "BB mid stopped falling with price near or above the midline", self.name, self.timeframe)
        if prev_flat and prev_slope >= 0 and slope <= -down_turn_limit and below >= 0.8:
            return SignalResult(SignalDirection.SELL, self._confidence(row, -1), self.weight, "BB mid stopped rising with price near or below the midline", self.name, self.timeframe)
        for lookback in range(max(1, pos - 4), pos):
            candidate = records[lookback]
            candidate_prev = records[lookback - 1]
            recent_crossed_up = candidate_prev["close"] < candidate_prev["bb_mid"] and candidate["close"] > candidate["bb_mid"]
            recent_crossed_down = candidate_prev["close"] > candidate_prev["bb_mid"] and candidate["close"] < candidate["bb_mid"]
            if recent_crossed_up and row["close"] > row["bb_mid"] and slope > 0 and not flat and above >= 0.8:
                return SignalResult(SignalDirection.BUY, self._confidence(row, 1), self.weight, "Recent BB mid cross confirmed by flattening/rising midline", self.name, self.timeframe)
            if recent_crossed_down and row["close"] < row["bb_mid"] and slope <= -down_turn_limit and below >= 0.8:
                return SignalResult(SignalDirection.SELL, self._confidence(row, -1), self.weight, "Recent BB mid cross confirmed by flattening/falling midline", self.name, self.timeframe)
        if crossed_up and slope > 0 and not flat:
            return SignalResult(SignalDirection.BUY, self._confidence(row, 1), self.weight, "Closed across BB mid with upward non-flat midline", self.name, self.timeframe)
        if crossed_down and slope < 0 and not flat:
            return SignalResult(SignalDirection.SELL, self._confidence(row, -1), self.weight, "Closed across BB mid with downward non-flat midline", self.name, self.timeframe)
        if crossed_down and tight_flat and below >= 0.9:
            return SignalResult(SignalDirection.SELL, self._confidence(row, -1), self.weight, "Closed below BB mid while midline flattened", self.name, self.timeframe)
        if crossed_up and tight_flat and above >= 0.9:
            return SignalResult(SignalDirection.BUY, self._confidence(row, 1), self.weight, "Closed above BB mid while midline flattened", self.name, self.timeframe)
        if pd.notna(atr) and body < atr * 0.1:
            return self.neutral("Skipped doji-like candle")
        if prev_below > body_threshold and row["close"] > row["bb_mid"] and above > body_threshold and slope > 0 and not flat:
            return SignalResult(SignalDirection.BUY, self._confidence(row, 1), self.weight, "Majority body crossed above BB mid with upward non-flat midline", self.name, self.timeframe)
        if prev_above > body_threshold and row["close"] < row["bb_mid"] and below > body_threshold and slope < 0 and not flat:
            return SignalResult(SignalDirection.SELL, self._confidence(row, -1), self.weight, "Majority body crossed below BB mid with downward non-flat midline", self.name, self.timeframe)
        return self.neutral("No majority-body BB midline cross")

    def _body_side_ratios(self, row) -> tuple[float, float]:
        top = max(row["open"], row["close"])
        bottom = min(row["open"], row["close"])
        body = top - bottom
        if body <= 0:
            return 0.0, 0.0
        mid = row["bb_mid"]
        above = max(0.0, top - max(mid, bottom)) / body
        below = max(0.0, min(mid, top) - bottom) / body
        return min(1.0, above), min(1.0, below)

    def _cold_start_signal(self, records: list[dict], pos: int) -> SignalResult:
        row = records[pos]
        close = row["close"]
        mid = row["bb_mid"]
        slope = row["bb_mid_slope"]
        if pd.isna(close) or pd.isna(mid) or pd.isna(slope) or is_bb_mid_flat(row, self.timeframe):
            return self.neutral("No trend-side BB mid confirmation")
        confidence = self._cold_start_confidence(row)
        if close > mid and slope > 0:
            return SignalResult(SignalDirection.BUY, confidence, self.weight, "Trend-side long: close is above BB mid with rising midline", self.name, self.timeframe)
        if close < mid and slope < 0:
            return SignalResult(SignalDirection.SELL, confidence, self.weight, "Trend-side short: close is below BB mid with falling midline", self.name, self.timeframe)
        return self.neutral("No trend-side direction confirmation")

    def _confidence(self, row, direction: int) -> float:
        bandwidth = row.get("bb_bandwidth")
        if pd.isna(bandwidth) or bandwidth <= 0:
            bandwidth = max(abs(row["bb_mid"]) * 0.01, 1e-9)
        distance = abs(row["close"] - row["bb_mid"]) / max(abs(row["bb_mid"]) * bandwidth, 1e-9)
        slope = row["bb_mid_slope"] * direction
        slope_boost = min(max(slope / max(abs(row["close"]) * 0.0005, 1e-9), 0.0), 0.2)
        flat_penalty = 0.08 if is_bb_mid_flat(row, self.timeframe) else 0.0
        return max(0.1, min(1.0, 0.5 + min(distance, 0.3) + slope_boost - flat_penalty))

    def _cold_start_confidence(self, row) -> float:
        slope = abs(row["bb_mid_slope"]) / max(abs(row["close"]) * 0.0005, 1e-9)
        return max(0.3, min(0.5, 0.35 + min(slope, 0.15)))


@register
class BBMidChangeDir(BBMidCross):
    name = "bb_mid_change_dir"
    display_name = "Bollinger Midline Change Direction"
    description = "Detects BB middle-line slope direction changes without requiring price to cross BB mid."
    strategy_notes = [
        "Entry rules: buy when BB mid slope changes from falling or flat to rising.",
        "Entry rules: sell when BB mid slope changes from rising or flat to falling.",
        "Exit rules: close and reverse when BB mid slope changes to the opposite direction.",
        "Filters: does not require the candle close or body to cross BB mid.",
        "Filters: do not open a new position before 09:45.",
        "Filters: uses the same entry-time gate as Bollinger Midline Cross.",
        "Best conditions: works best when the BB midline turn itself leads price rotation.",
        "Weaknesses: can flip early if the midline wiggles before price confirms.",
    ]

    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        results: list[SignalResult] = []
        records = df.to_dict("records")
        for pos, row in enumerate(records):
            if pos == 0:
                results.append(self.neutral("Previous candle unavailable"))
                continue
            prev = records[pos - 1]
            if pd.isna(row["bb_mid"]) or pd.isna(prev["bb_mid"]) or pd.isna(row["bb_mid_slope"]):
                results.append(self.neutral("Bollinger midline unavailable"))
                continue
            signal = self._change_direction_signal(row, prev)
            results.append(signal)
        return pd.Series(results, index=df.index)

    def _change_direction_signal(self, row, prev) -> SignalResult:
        slope = row["bb_mid_slope"]
        prev_slope = prev.get("bb_mid_slope")
        if pd.isna(slope) or pd.isna(prev_slope):
            return self.neutral("BB mid slope unavailable")
        if slope > 0 and prev_slope <= 0:
            return SignalResult(SignalDirection.BUY, self._confidence(row, 1), self.weight, "BB mid slope changed direction upward", self.name, self.timeframe)
        if slope < 0 and prev_slope >= 0:
            return SignalResult(SignalDirection.SELL, self._confidence(row, -1), self.weight, "BB mid slope changed direction downward", self.name, self.timeframe)
        return self.neutral("No BB mid direction change")


@register
class BBMidTrendRider(BBMidCross):
    name = "bb_mid_trend_rider"
    display_name = "Bollinger Midline Trend Rider"
    description = "Rides directional moves while price holds the trend side of a non-flat BB midline."
    strategy_notes = [
        "Entry rules: buy when BB mid is rising, price is holding above BB mid, and momentum is not bearish.",
        "Entry rules: sell when BB mid is falling, price is holding below BB mid, and momentum is not bullish.",
        "Trend filter: at least 2 of the last 3 closes must be on the trade side of BB mid.",
        "Exit rules: close and reverse when the opposite trend-side state appears.",
        "Filters: do not open a new position before 09:45.",
        "Best conditions: works best when price keeps respecting BB mid during an intraday trend.",
        "Weaknesses: can enter late after the trend is already extended.",
    ]

    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        results: list[SignalResult] = []
        records = df.to_dict("records")
        for pos, row in enumerate(records):
            if pos < 2:
                results.append(self.neutral("Trend-side lookback unavailable"))
                continue
            prev = records[pos - 1]
            if pd.isna(row["bb_mid"]) or pd.isna(prev["bb_mid"]) or pd.isna(row["bb_mid_slope"]):
                results.append(self.neutral("Bollinger midline unavailable"))
                continue
            results.append(self._trend_rider_signal(records, pos))
        return pd.Series(results, index=df.index)

    def _trend_rider_signal(self, records: list[dict], pos: int) -> SignalResult:
        row = records[pos]
        close = row["close"]
        mid = row["bb_mid"]
        slope = row["bb_mid_slope"]
        if pd.isna(close) or pd.isna(mid) or pd.isna(slope) or is_bb_mid_flat(row, self.timeframe):
            return self.neutral("No non-flat BB mid trend")
        above_count, below_count = self._recent_mid_side_counts(records, pos, 3)
        macd_hist = row.get("macd_hist")
        prev_hist = records[pos - 1].get("macd_hist")
        stoch_k = row.get("stoch_rsi_k")
        stoch_d = row.get("stoch_rsi_d")
        slope_up_count, slope_down_count = self._recent_slope_counts(records, pos, 3)
        distance = self._distance_from_mid(row)
        momentum_long_ok = self._momentum_ok(macd_hist, prev_hist, stoch_k, stoch_d, 1)
        momentum_short_ok = self._momentum_ok(macd_hist, prev_hist, stoch_k, stoch_d, -1)
        if slope > 0 and close > mid and above_count >= 2 and slope_up_count >= 2 and distance <= 0.5 and momentum_long_ok:
            return SignalResult(SignalDirection.BUY, self._trend_confidence(row, above_count, 1), self.weight, "Trend rider long: price held above rising BB mid", self.name, self.timeframe)
        if slope < 0 and close < mid and below_count >= 2 and slope_down_count >= 2 and distance <= 0.5 and momentum_short_ok:
            return SignalResult(SignalDirection.SELL, self._trend_confidence(row, below_count, -1), self.weight, "Trend rider short: price held below falling BB mid", self.name, self.timeframe)
        return self.neutral("No confirmed BB mid trend-side state")

    def _recent_mid_side_counts(self, records: list[dict], pos: int, lookback: int) -> tuple[int, int]:
        above = 0
        below = 0
        for row in records[max(0, pos - lookback + 1):pos + 1]:
            close = row.get("close")
            mid = row.get("bb_mid")
            if pd.isna(close) or pd.isna(mid):
                continue
            above += int(close > mid)
            below += int(close < mid)
        return above, below

    def _recent_slope_counts(self, records: list[dict], pos: int, lookback: int) -> tuple[int, int]:
        up = 0
        down = 0
        for row in records[max(0, pos - lookback + 1):pos + 1]:
            slope = row.get("bb_mid_slope")
            if pd.isna(slope):
                continue
            up += int(slope > 0)
            down += int(slope < 0)
        return up, down

    def _momentum_ok(self, macd_hist, prev_hist, stoch_k, stoch_d, direction: int) -> bool:
        macd_ok = True
        if pd.notna(macd_hist):
            if direction > 0:
                macd_ok = macd_hist >= 0 and (pd.isna(prev_hist) or macd_hist >= prev_hist)
            else:
                macd_ok = macd_hist <= 0 and (pd.isna(prev_hist) or macd_hist <= prev_hist)
        stoch_ok = True
        if pd.notna(stoch_k) and pd.notna(stoch_d):
            if direction > 0:
                stoch_ok = stoch_k >= stoch_d and stoch_k > 35
            else:
                stoch_ok = stoch_k <= stoch_d and stoch_k < 65
        return macd_ok and stoch_ok

    def _distance_from_mid(self, row) -> float:
        bandwidth = row.get("bb_bandwidth")
        if pd.isna(bandwidth) or bandwidth <= 0:
            bandwidth = max(abs(row["bb_mid"]) * 0.01, 1e-9)
        return abs(row["close"] - row["bb_mid"]) / max(abs(row["bb_mid"]) * bandwidth, 1e-9)

    def _trend_confidence(self, row, side_count: int, direction: int) -> float:
        base = self._confidence(row, direction)
        hold_bonus = 0.08 if side_count >= 3 else 0.03
        return max(0.1, min(1.0, base + hold_bonus))

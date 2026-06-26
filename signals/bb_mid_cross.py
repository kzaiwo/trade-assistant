import pandas as pd

from indicators.bollinger_bands import BB_MID_FLAT_TOLERANCE, BollingerBands
from models.types import SignalDirection, SignalResult
from signals.base import Signal
from signals.registry import register


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
        "Filters: skip small doji-like higher-timeframe candles and use Chop Filter to block repeated flat midline crosses or tight bandwidth without expansion.",
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
        flat = bool(row.get("bb_mid_flat", False))
        crossed_up = prev["close"] < prev["bb_mid"] and row["close"] > row["bb_mid"]
        crossed_down = prev["close"] > prev["bb_mid"] and row["close"] < row["bb_mid"]
        flat_limit = row["close"] * BB_MID_FLAT_TOLERANCE
        tight_flat = abs(slope) <= row["close"] * (BB_MID_FLAT_TOLERANCE * 0.1)
        prev_flat = pd.notna(prev_slope) and abs(prev_slope) <= flat_limit
        prev_falling = pd.notna(prev_slope) and prev_slope < -flat_limit
        near_mid = abs(row["close"] - row["bb_mid"]) <= flat_limit
        down_turn_limit = flat_limit * 0.37
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
        if pd.isna(close) or pd.isna(mid) or pd.isna(slope) or bool(row.get("bb_mid_flat", False)):
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
        flat_penalty = 0.08 if bool(row.get("bb_mid_flat", False)) else 0.0
        return max(0.1, min(1.0, 0.5 + min(distance, 0.3) + slope_boost - flat_penalty))

    def _cold_start_confidence(self, row) -> float:
        slope = abs(row["bb_mid_slope"]) / max(abs(row["close"]) * 0.0005, 1e-9)
        return max(0.3, min(0.5, 0.35 + min(slope, 0.15)))

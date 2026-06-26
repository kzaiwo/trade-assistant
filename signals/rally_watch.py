from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd

from indicators.bollinger_bands import BollingerBands
from indicators.macd import MACD
from indicators.stoch_rsi import StochRSI
from indicators.vwap import VWAP
from models.types import SignalDirection, SignalResult
from signals.base import Signal
from signals.price_levels import PriceLevelsResult
from signals.registry import register


RALLY_WEIGHTS = {
    "bb_mid_slope_steepening": 15,
    "close_vs_bb_mid": 10,
    "close_vs_vwap": 10,
    "macd_hist_expanding": 15,
    "stoch_rsi_turn": 10,
    "bb_bandwidth_expanding": 15,
    "volume_above_avg": 10,
    "candle_body_strength": 10,
    "higher_tf_agreement": 15,
}


@dataclass
class RallyScore:
    direction: str
    score: int
    state: str
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RallyWatchResult:
    up: RallyScore
    down: RallyScore
    targets: PriceLevelsResult | None
    timestamp: str
    timeframe: str

    def to_dict(self) -> dict:
        return {
            "up": self.up.to_dict(),
            "down": self.down.to_dict(),
            "targets": self.targets.to_dict() if self.targets else None,
            "timestamp": self.timestamp,
            "timeframe": self.timeframe,
        }


def get_state(score: int) -> str:
    if score >= 75:
        return "strong"
    if score >= 60:
        return "likely"
    if score >= 40:
        return "building"
    return "no_setup"


def _num(value, default: float | None = None) -> float | None:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_pct(value: float) -> str:
    return f"{value * 100:.3f}%"


def _safe_records(df: pd.DataFrame | None) -> list[dict]:
    if df is None or df.empty:
        return []
    return df.to_dict("records")


def _score_close_vs_level(close: float | None, level: float | None, direction: str, weight: int, scale: float) -> tuple[float, str | None, bool]:
    if close is None or level is None or close <= 0:
        return 0.0, None, False
    delta = (close - level) / close
    directional = delta > 0 if direction == "up" else delta < 0
    if not directional:
        return 0.0, None, True
    strength = min(1.0, abs(delta) / max(scale, 0.0001))
    side = "above" if delta > 0 else "below"
    return weight * max(0.45, strength), f"Price {side} level ({_fmt_pct(abs(delta))})", False


def _higher_timeframe_agrees(records: list[dict], direction: str) -> tuple[float, str | None]:
    if not records:
        return 0.0, None
    bar = records[-1]
    close = _num(bar.get("close"))
    mid = _num(bar.get("bb_mid"))
    slope = _num(bar.get("bb_mid_slope") or bar.get("bb_slope"))
    hist = _num(bar.get("macd_hist"))
    if close is None or mid is None:
        return 0.0, None
    votes = 0
    possible = 0
    possible += 1
    votes += int((close > mid) if direction == "up" else (close < mid))
    if slope is not None:
        possible += 1
        votes += int((slope > 0) if direction == "up" else (slope < 0))
    if hist is not None:
        possible += 1
        votes += int((hist > 0) if direction == "up" else (hist < 0))
    ratio = votes / max(1, possible)
    if ratio >= 0.67:
        return RALLY_WEIGHTS["higher_tf_agreement"] * ratio, "Higher timeframe agrees"
    if ratio >= 0.34:
        return RALLY_WEIGHTS["higher_tf_agreement"] * 0.35, "Higher timeframe is mixed"
    return 0.0, None


def _score_direction(records: list[dict], direction: str, higher_records: list[dict] | None = None) -> RallyScore:
    if not records:
        return RallyScore(direction, 0, "no_setup", [], ["No candles available"])
    index = len(records) - 1
    bar = records[index]
    prev = records[index - 1] if index > 0 else {}
    close = _num(bar.get("close"))
    open_ = _num(bar.get("open"))
    high = _num(bar.get("high"))
    low = _num(bar.get("low"))
    mid = _num(bar.get("bb_mid"))
    slope = _num(bar.get("bb_mid_slope") or bar.get("bb_slope"))
    prev_slope = _num(prev.get("bb_mid_slope") or prev.get("bb_slope"))
    upper = _num(bar.get("bb_upper"))
    lower = _num(bar.get("bb_lower"))
    vwap = _num(bar.get("vwap"))
    hist = _num(bar.get("macd_hist"))
    prev_hist = _num(prev.get("macd_hist"))
    k = _num(bar.get("stoch_rsi_k"))
    prev_k = _num(prev.get("stoch_rsi_k"))
    bandwidth = _num(bar.get("bb_bandwidth"))
    prev_width = _num(prev.get("bb_bandwidth"))
    volume = _num(bar.get("volume"))
    volume_values = [_num(item.get("volume")) for item in records[max(0, index - 20):index]]
    volume_values = [v for v in volume_values if v is not None]
    avg_volume = sum(volume_values) / len(volume_values) if volume_values else None
    reasons: list[str] = []
    warnings: list[str] = []
    raw_score = 0.0

    sign = 1 if direction == "up" else -1
    slope_in_direction = slope is not None and slope * sign > 0
    steepening = slope_in_direction and (prev_slope is None or abs(slope) > abs(prev_slope))
    if steepening:
        raw_score += RALLY_WEIGHTS["bb_mid_slope_steepening"]
        reasons.append(f"BB mid {'rising' if direction == 'up' else 'falling'} faster")
    elif slope_in_direction:
        raw_score += RALLY_WEIGHTS["bb_mid_slope_steepening"] * 0.5
        reasons.append(f"BB mid {'rising' if direction == 'up' else 'falling'}")

    if close is not None and upper is not None and lower is not None and mid:
        width_scale = max(abs(upper - lower) / max(abs(mid), 1), 0.0008)
    else:
        width_scale = 0.0015

    points, reason, _ = _score_close_vs_level(close, mid, direction, RALLY_WEIGHTS["close_vs_bb_mid"], width_scale * 0.4)
    raw_score += points
    if reason:
        reasons.append(reason.replace("level", "BB mid"))

    points, reason, against_vwap = _score_close_vs_level(close, vwap, direction, RALLY_WEIGHTS["close_vs_vwap"], 0.0018)
    raw_score += points
    if reason:
        reasons.append(reason.replace("level", "VWAP"))
    elif against_vwap:
        warnings.append("Against VWAP")

    hist_in_direction = hist is not None and hist * sign > 0
    hist_expanding = hist_in_direction and (prev_hist is None or abs(hist) > abs(prev_hist))
    if hist_expanding:
        raw_score += RALLY_WEIGHTS["macd_hist_expanding"]
        reasons.append(f"MACD hist expanding ({hist:+.3f})")
    elif hist_in_direction:
        raw_score += RALLY_WEIGHTS["macd_hist_expanding"] * 0.45
        reasons.append(f"MACD hist supports {direction}")

    if k is not None:
        if direction == "up":
            if k > 80:
                warnings.append("Overextended")
            elif prev_k is not None and k > prev_k and prev_k < 30:
                raw_score += RALLY_WEIGHTS["stoch_rsi_turn"]
                reasons.append(f"StochRSI turning up from {prev_k:.1f}")
            elif prev_k is not None and k > prev_k and k < 70:
                raw_score += RALLY_WEIGHTS["stoch_rsi_turn"] * 0.5
                reasons.append("StochRSI rising")
        else:
            if k < 20:
                warnings.append("Overextended")
            elif prev_k is not None and k < prev_k and prev_k > 70:
                raw_score += RALLY_WEIGHTS["stoch_rsi_turn"]
                reasons.append(f"StochRSI turning down from {prev_k:.1f}")
            elif prev_k is not None and k < prev_k and k > 30:
                raw_score += RALLY_WEIGHTS["stoch_rsi_turn"] * 0.5
                reasons.append("StochRSI falling")

    if bandwidth is not None and prev_width is not None:
        expanding = bandwidth > prev_width
        prior_values = [_num(item.get("bb_bandwidth")) for item in records[max(0, index - 8):index]]
        prior_values = [v for v in prior_values if v is not None]
        prior_avg = sum(prior_values) / len(prior_values) if prior_values else prev_width
        accelerating = expanding and bandwidth > prior_avg
        if accelerating:
            raw_score += RALLY_WEIGHTS["bb_bandwidth_expanding"]
            reasons.append("Bandwidth expanding after squeeze")
        elif expanding:
            raw_score += RALLY_WEIGHTS["bb_bandwidth_expanding"] * 0.55
            reasons.append("Bandwidth expanding")
        elif bandwidth < 0.006:
            warnings.append("Narrow bandwidth")

    if volume is not None and avg_volume and avg_volume > 0:
        volume_ratio = volume / avg_volume
        if volume_ratio >= 1:
            raw_score += RALLY_WEIGHTS["volume_above_avg"] * min(1.0, max(0.35, (volume_ratio - 0.7) / 0.8))
            reasons.append(f"Volume {volume_ratio:.1f}x average")
        elif volume_ratio < 0.7:
            warnings.append("Low volume")

    strong_count = 0
    for item in records[max(0, index - 2):index + 1]:
        item_open = _num(item.get("open"))
        item_close = _num(item.get("close"))
        item_high = _num(item.get("high"))
        item_low = _num(item.get("low"))
        if None in {item_open, item_close, item_high, item_low}:
            continue
        candle_range = max(item_high - item_low, 0)
        body = abs(item_close - item_open)
        if candle_range <= 0 or body / candle_range < 0.6:
            continue
        wick_against = (min(item_open, item_close) - item_low) if direction == "up" else (item_high - max(item_open, item_close))
        closes_direction = item_close > item_open if direction == "up" else item_close < item_open
        if closes_direction and wick_against <= candle_range * 0.28:
            strong_count += 1
    if strong_count:
        raw_score += RALLY_WEIGHTS["candle_body_strength"] * min(1.0, strong_count / 3)
        reasons.append(f"{strong_count} strong {'bullish' if direction == 'up' else 'bearish'} candle{'s' if strong_count != 1 else ''}")

    points, reason = _higher_timeframe_agrees(higher_records or [], direction)
    raw_score += points
    if reason:
        reasons.append(reason)

    if slope is not None and close is not None and abs(slope) <= close * 0.0003:
        warnings.append("Flat BB mid")

    max_score = sum(RALLY_WEIGHTS.values())
    score = int(round(max(0.0, min(100.0, raw_score / max_score * 100))))
    return RallyScore(direction, score, get_state(score), reasons[:8], list(dict.fromkeys(warnings))[:5])


def evaluate_rally_watch(
    df: pd.DataFrame,
    timeframe: str = "1m",
    higher_df: pd.DataFrame | None = None,
    price_levels: PriceLevelsResult | None = None,
) -> RallyWatchResult:
    records = _safe_records(df)
    higher_records = _safe_records(higher_df)
    timestamp = str(records[-1].get("time_key") or records[-1].get("date") or "") if records else ""
    return RallyWatchResult(
        up=_score_direction(records, "up", higher_records),
        down=_score_direction(records, "down", higher_records),
        targets=price_levels,
        timestamp=timestamp,
        timeframe=timeframe,
    )


@register
class RallyWatch(Signal):
    name = "rally_watch"
    display_name = "Rally Watch"
    description = "Scores whether an upside or downside rally is forming without producing trade entries by default."
    category = "alert"
    required_indicators = [BollingerBands, MACD, StochRSI, VWAP]
    default_params = {"min_score": 60}
    strategy_notes = [
        "Alert rules: scores rally-up and rally-down probability from BB mid slope, VWAP, MACD, StochRSI, bandwidth, volume, candle bodies, and higher-timeframe agreement.",
        "Entry rules: this is informational by default and is not wired into any strategy.",
        "Future filter mode: strategies may use min_score to require a rally score before acting.",
    ]

    def score(self, df: pd.DataFrame, higher_df: pd.DataFrame | None = None) -> RallyWatchResult:
        return evaluate_rally_watch(df, self.timeframe, higher_df)

    def evaluate(self, df: pd.DataFrame) -> pd.Series:
        min_score = int(self.params.get("min_score", 60))
        results: list[SignalResult] = []
        for pos in range(len(df)):
            window = df.iloc[:pos + 1]
            score = evaluate_rally_watch(window, self.timeframe)
            if score.up.score >= min_score and score.up.score > score.down.score:
                results.append(SignalResult(SignalDirection.BUY, score.up.score / 100, self.weight, "Rally Watch upside score met filter threshold", self.name, self.timeframe))
            elif score.down.score >= min_score and score.down.score > score.up.score:
                results.append(SignalResult(SignalDirection.SELL, score.down.score / 100, self.weight, "Rally Watch downside score met filter threshold", self.name, self.timeframe))
            else:
                results.append(self.neutral("Rally Watch score below filter threshold"))
        return pd.Series(results, index=df.index)

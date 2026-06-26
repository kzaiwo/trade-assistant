from __future__ import annotations

from dataclasses import asdict, dataclass, field

import pandas as pd


@dataclass
class PriceLevel:
    price: float
    level_type: str
    source: str
    strength: float
    recency: int
    label: str = ""
    confluent_with: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["price"] = round(float(self.price), 6)
        data["strength"] = round(float(self.strength), 6)
        return data


@dataclass
class PriceTarget:
    price: float
    direction: str
    label: str
    source: str
    distance_dollars: float
    distance_pct: float

    def to_dict(self) -> dict:
        data = asdict(self)
        data["price"] = round(float(self.price), 6)
        data["distance_dollars"] = round(float(self.distance_dollars), 6)
        data["distance_pct"] = round(float(self.distance_pct), 6)
        return data


@dataclass
class InvalidationLevel:
    price: float
    direction: str
    reason: str
    distance_dollars: float = 0.0
    distance_pct: float = 0.0

    def to_dict(self) -> dict:
        data = asdict(self)
        data["price"] = round(float(self.price), 6)
        data["distance_dollars"] = round(float(self.distance_dollars), 6)
        data["distance_pct"] = round(float(self.distance_pct), 6)
        return data


@dataclass
class PriceLevelsResult:
    supports: list[PriceLevel]
    resistances: list[PriceLevel]
    targets_up: list[PriceTarget]
    targets_down: list[PriceTarget]
    invalidations: list[InvalidationLevel]
    current_price: float
    timestamp: str
    timeframe: str

    def to_dict(self) -> dict:
        return {
            "supports": [item.to_dict() for item in self.supports],
            "resistances": [item.to_dict() for item in self.resistances],
            "targets_up": [item.to_dict() for item in self.targets_up],
            "targets_down": [item.to_dict() for item in self.targets_down],
            "invalidations": [item.to_dict() for item in self.invalidations],
            "current_price": round(float(self.current_price), 6),
            "timestamp": self.timestamp,
            "timeframe": self.timeframe,
        }


def _num(value, default: float | None = None) -> float | None:
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _records(df: pd.DataFrame | None) -> list[dict]:
    if df is None or df.empty:
        return []
    return df.to_dict("records")


def _source_label(source: str) -> str:
    return source.replace("_", " ")


def _add_indicator_levels(levels: list[PriceLevel], bar: dict, current_price: float) -> None:
    mapping = [
        ("vwap", "vwap"),
        ("bb_upper", "bb_upper"),
        ("bb_mid", "bb_mid"),
        ("bb_lower", "bb_lower"),
    ]
    for source, key in mapping:
        price = _num(bar.get(key))
        if price is None or price <= 0:
            continue
        level_type = "support" if price < current_price else "resistance"
        levels.append(PriceLevel(price, level_type, source, 1.0, 0, confluent_with=[source]))


def _add_session_levels(levels: list[PriceLevel], records: list[dict], current_price: float) -> None:
    highs = [(_num(item.get("high")), idx) for idx, item in enumerate(records)]
    lows = [(_num(item.get("low")), idx) for idx, item in enumerate(records)]
    highs = [(price, idx) for price, idx in highs if price is not None]
    lows = [(price, idx) for price, idx in lows if price is not None]
    if highs:
        price, idx = max(highs, key=lambda item: item[0])
        levels.append(PriceLevel(price, "resistance" if price >= current_price else "support", "session_high", 1.0, len(records) - 1 - idx, confluent_with=["session_high"]))
    if lows:
        price, idx = min(lows, key=lambda item: item[0])
        levels.append(PriceLevel(price, "support" if price <= current_price else "resistance", "session_low", 1.0, len(records) - 1 - idx, confluent_with=["session_low"]))


def _add_previous_day_levels(levels: list[PriceLevel], records: list[dict], current_price: float) -> None:
    if not records:
        return
    current_day = str(records[-1].get("date") or str(records[-1].get("time_key", ""))[:10])
    prior = [item for item in records if str(item.get("date") or str(item.get("time_key", ""))[:10]) < current_day]
    if not prior:
        return
    last_prior_day = max(str(item.get("date") or str(item.get("time_key", ""))[:10]) for item in prior)
    prior = [item for item in prior if str(item.get("date") or str(item.get("time_key", ""))[:10]) == last_prior_day]
    high = max((_num(item.get("high")) for item in prior), default=None)
    low = min((_num(item.get("low")) for item in prior), default=None)
    if high:
        levels.append(PriceLevel(high, "resistance" if high >= current_price else "support", "prev_day_high", 0.9, len(records), confluent_with=["prev_day_high"]))
    if low:
        levels.append(PriceLevel(low, "support" if low <= current_price else "resistance", "prev_day_low", 0.9, len(records), confluent_with=["prev_day_low"]))


def _add_swing_levels(levels: list[PriceLevel], records: list[dict], current_price: float, max_lookback: int, pivot: int) -> None:
    start = max(0, len(records) - max_lookback)
    window = records[start:]
    swings: list[PriceLevel] = []
    for local_idx in range(pivot, max(pivot, len(window) - pivot)):
        item = window[local_idx]
        high = _num(item.get("high"))
        low = _num(item.get("low"))
        if high is None or low is None:
            continue
        prior = window[local_idx - pivot:local_idx]
        after = window[local_idx + 1:local_idx + pivot + 1]
        if len(after) < pivot:
            continue
        prior_highs = [_num(candidate.get("high")) for candidate in prior]
        after_highs = [_num(candidate.get("high")) for candidate in after]
        prior_lows = [_num(candidate.get("low")) for candidate in prior]
        after_lows = [_num(candidate.get("low")) for candidate in after]
        candles_ago = len(window) - 1 - local_idx
        strength = max(0.3, 1.0 - candles_ago / max(max_lookback, 1))
        if all(value is not None and high > value for value in prior_highs + after_highs):
            swings.append(PriceLevel(high, "resistance" if high >= current_price else "support", "swing_high", strength, candles_ago, confluent_with=["swing_high"]))
        if all(value is not None and low < value for value in prior_lows + after_lows):
            swings.append(PriceLevel(low, "support" if low <= current_price else "resistance", "swing_low", strength, candles_ago, confluent_with=["swing_low"]))
    swings.sort(key=lambda item: item.recency)
    levels.extend(swings[:10])


def detect_confluence(levels: list[PriceLevel], threshold_pct: float = 0.001) -> list[PriceLevel]:
    remaining = sorted(levels, key=lambda item: item.price)
    merged: list[PriceLevel] = []
    while remaining:
        seed = remaining.pop(0)
        cluster = [seed]
        keep: list[PriceLevel] = []
        for item in remaining:
            if abs(item.price - seed.price) / max(abs(seed.price), 1) <= threshold_pct:
                cluster.append(item)
            else:
                keep.append(item)
        remaining = keep
        if len(cluster) == 1:
            merged.append(seed)
            continue
        primary = max(cluster, key=lambda item: item.strength)
        sources = []
        for item in cluster:
            sources.extend(item.confluent_with or [item.source])
        unique_sources = list(dict.fromkeys(sources))
        strength = min(1.0, max(item.strength for item in cluster) + 0.2 * (len(unique_sources) - 1))
        price = sum(item.price for item in cluster) / len(cluster)
        merged.append(
            PriceLevel(
                price=price,
                level_type=primary.level_type,
                source=primary.source,
                strength=strength,
                recency=min(item.recency for item in cluster),
                confluent_with=unique_sources,
            )
        )
    return merged


def _label_levels(levels: list[PriceLevel], prefix: str) -> list[PriceLevel]:
    out = []
    for idx, item in enumerate(levels[:4], start=1):
        out.append(PriceLevel(item.price, item.level_type, item.source, item.strength, item.recency, f"{prefix}{idx}", item.confluent_with))
    return out


def _targets(levels: list[PriceLevel], direction: str, current_price: float) -> list[PriceTarget]:
    targets = []
    for idx, level in enumerate(levels[:3], start=1):
        distance = level.price - current_price if direction == "up" else current_price - level.price
        pct = distance / current_price * 100 if current_price else 0.0
        targets.append(PriceTarget(level.price, direction, f"T{idx}", level.source, distance, pct))
    return targets


def _invalidations(records: list[dict], current_price: float) -> list[InvalidationLevel]:
    if not records:
        return []
    bar = records[-1]
    candidates = []
    for source, reason_up, reason_down in [
        ("bb_mid", "Loses BB mid", "Reclaims BB mid"),
        ("vwap", "Loses VWAP", "Reclaims VWAP"),
    ]:
        price = _num(bar.get(source))
        if price is None:
            continue
        candidates.append((source, price, reason_up, reason_down))
    below = [(price, reason) for _, price, reason, _ in candidates if price < current_price]
    above = [(price, reason) for _, price, _, reason in candidates if price > current_price]
    out = []
    if below:
        price, reason = max(below, key=lambda item: item[0])
        distance = current_price - price
        out.append(InvalidationLevel(price, "up", reason, distance, distance / current_price * 100 if current_price else 0.0))
    if above:
        price, reason = min(above, key=lambda item: item[0])
        distance = price - current_price
        out.append(InvalidationLevel(price, "down", reason, distance, distance / current_price * 100 if current_price else 0.0))
    return out


def evaluate_price_levels(
    df: pd.DataFrame,
    timeframe: str = "1m",
    swing_lookback: int = 50,
    pivot: int = 3,
    confluence_threshold_pct: float = 0.001,
) -> PriceLevelsResult:
    records = _records(df)
    if not records:
        return PriceLevelsResult([], [], [], [], [], 0.0, "", timeframe)
    current = records[-1]
    current_price = _num(current.get("close"), 0.0) or 0.0
    levels: list[PriceLevel] = []
    _add_swing_levels(levels, records, current_price, swing_lookback, pivot)
    _add_session_levels(levels, records, current_price)
    _add_previous_day_levels(levels, records, current_price)
    _add_indicator_levels(levels, current, current_price)
    levels = detect_confluence(levels, confluence_threshold_pct)
    supports = [item for item in levels if item.price < current_price]
    resistances = [item for item in levels if item.price > current_price]
    supports.sort(key=lambda item: (abs(current_price - item.price), -item.strength))
    resistances.sort(key=lambda item: (abs(item.price - current_price), -item.strength))
    supports = _label_levels(supports, "S")
    resistances = _label_levels(resistances, "R")
    return PriceLevelsResult(
        supports=supports,
        resistances=resistances,
        targets_up=_targets(resistances, "up", current_price),
        targets_down=_targets(supports, "down", current_price),
        invalidations=_invalidations(records, current_price),
        current_price=current_price,
        timestamp=str(current.get("time_key") or current.get("date") or ""),
        timeframe=timeframe,
    )

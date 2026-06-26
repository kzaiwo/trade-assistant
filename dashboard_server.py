from __future__ import annotations

import json
import math
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from config import BACKTEST_NOTIONAL


ROOT = Path(__file__).resolve().parent
DATA_ROOT = (ROOT / "../_trade_data").resolve()
RESULTS_PATH = ROOT / "results/backtest_summary.json"
SYMBOLS = ["AAPL", "TSLA", "MU", "INTC"]
STRATEGY_NAMES = ["bb_squeeze", "bb_breakout", "stoch_cross", "vwap_bounce", "macd_cross", "bb_mid_cross", "mean_reversion"]
TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "60m"]
BARS_CACHE: dict[tuple[str, str, str, str], tuple[list[dict], Path]] = {}
BB_MID_FLAT_TOLERANCE = 0.001
BB_MID_FIRST_ENTRY_TIME = "09:45"


def _plain_symbol(value: str) -> str:
    return value.strip().upper().replace("US.", "") or "INTC"


def _timeframe_hint(ktype: str) -> str:
    minutes = _timeframe_minutes(ktype)
    return f"{minutes}min"


def _timeframe_minutes(ktype: str) -> int:
    value = str(ktype or "1m").lower()
    if value.endswith("m") and value[:-1].isdigit():
        return max(1, int(value[:-1]))
    return 1


def _bars_interval_minutes(bars: list[dict]) -> int:
    for i in range(1, len(bars)):
        try:
            from datetime import datetime

            prev = datetime.strptime(str(bars[i - 1].get("time_key")), "%Y-%m-%d %H:%M:%S")
            curr = datetime.strptime(str(bars[i].get("time_key")), "%Y-%m-%d %H:%M:%S")
            diff = round((curr - prev).total_seconds() / 60)
            if 0 < diff < 240:
                return diff
        except (TypeError, ValueError):
            continue
    return 1


def _strategy_timeframes(ktype: str) -> list[str]:
    return [ktype] if ktype in TIMEFRAMES else ["1m"]


def _read_json(path: Path):
    with path.open() as fh:
        return json.load(fh)


def _load_bars(symbol: str, ktype: str, start: str, end: str) -> tuple[list[dict], Path]:
    cache_key = (symbol, ktype, start, end)
    if cache_key in BARS_CACHE:
        return BARS_CACHE[cache_key]
    folder = DATA_ROOT / symbol
    hint = _timeframe_hint(ktype)
    files = sorted(folder.glob(f"*_{hint}.json"))
    native_timeframe = bool(files)
    if not files and hint != "1min":
        files = sorted(folder.glob("*_1min.json"))
    if not files:
        files = sorted(folder.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"No data files found for {symbol} in {DATA_ROOT}")

    bars: list[dict] = []
    for file_path in files:
        payload = _read_json(file_path)
        bars.extend(payload.get("bars", payload if isinstance(payload, list) else []))

    filtered = []
    for bar in bars:
        day = str(bar.get("date") or str(bar.get("time_key", ""))[:10])
        if start and day < start:
            continue
        if end and day > end:
            continue
        normalized = dict(bar)
        normalized["date"] = day
        filtered.append(normalized)
    filtered.sort(key=lambda item: item.get("time_key", ""))
    minutes = _timeframe_minutes(ktype)
    if native_timeframe and minutes > 1:
        filtered = _close_labeled_bars(filtered, minutes)
    if not native_timeframe and minutes > 1:
        filtered = _compute_derived_indicators(_timeframe_bars(filtered, ktype))
    BARS_CACHE[cache_key] = (filtered, files[0])
    return filtered, files[0]


def _empty_strategy(strategy_id: str) -> dict:
    base_id = strategy_id.rsplit("_", 1)[0]
    label = base_id.replace("_", " ").title()
    return {
        "id": strategy_id,
        "label": label,
        "description": "Updated Python strategy available for this backtest run.",
        "stats": {
            "strategy": label,
            "trades": 0,
            "pnl_per_share": 0,
            "pnl_total": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "max_dd": 0,
        },
        "trades": [],
        "rules": {
            "label": label,
            "description": "Strategy supplied by main.py/build_strategies.",
            "note": "Candle data is loaded locally; full trade entries require the Python backtest summary.",
            "strategy_notes": [
                "Entry rules: open when the strategy emits a buy or sell signal.",
                "Exit rules: close when the strategy emits the opposite signal.",
                "Filters: depends on the strategy implementation.",
                "Best conditions: depends on the selected strategy.",
                "Weaknesses: requires a completed Python backtest summary for full trade detail.",
            ],
            "entry": [["Signal", "Open long on buy signal.", "Open short on sell signal."]],
            "exit": [["Opposite signal", "Close long when signal flips.", "Close short when signal flips."]],
        },
    }


def _strategy_payload(ktype: str = "1m") -> dict:
    strategies = [_empty_strategy(f"{name}_{tf}") for tf in _strategy_timeframes(ktype) for name in STRATEGY_NAMES]
    return {"strategies": strategies, "defaultStrategy": strategies[0]["id"]}


def _pnl(side: str, entry: float, exit_price: float) -> float:
    return exit_price - entry if side == "LONG" else entry - exit_price


def _summarize_dashboard_trades(label: str, trades: list[dict]) -> dict:
    pnl = sum(t["pnl"] for t in trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    gross_win = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gross_loss = abs(sum(t["pnl"] for t in trades if t["pnl"] <= 0))
    running = 0.0
    peak = 0.0
    max_dd = 0.0
    for trade in trades:
        running += trade["pnl"]
        peak = max(peak, running)
        max_dd = min(max_dd, running - peak)
    return {
        "strategy": label,
        "trades": len(trades),
        "pnl_per_share": round(pnl, 6),
        "pnl_total": round(sum(t.get("pnlTotal", 0.0) for t in trades), 6),
        "win_rate": round((wins / len(trades) * 100) if trades else 0.0, 3),
        "profit_factor": round(gross_win / gross_loss, 6) if gross_loss else (999 if gross_win else 0),
        "max_dd": round(max_dd, 6),
    }


def _run_dashboard_strategies(payload: dict) -> dict:
    bars = payload.get("bars") or []
    symbol = _plain_symbol(str(payload.get("symbol") or "INTC"))
    ktype = str(payload.get("ktype") or "1m")
    if not bars and payload.get("start") and payload.get("end"):
        bars, _ = _load_bars(symbol, ktype, str(payload.get("start") or ""), str(payload.get("end") or ""))
    session = str(payload.get("session") or "all")
    bars = _filter_session(bars, session)
    if not bars:
        return _run_fast_dashboard_strategies([], symbol, ktype)
    return _run_fast_dashboard_strategies(bars, symbol, ktype)


def _num(value, default: float | None = None):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _session_matches(bar: dict, session: str) -> bool:
    if session == "all":
        return True
    ranges = {
        "pre": ("04:00", "09:29"),
        "main": ("09:30", "16:00"),
        "post": ("16:01", "20:00"),
        "night": ("20:01", "03:59"),
    }
    start, end = ranges.get(session, ("00:00", "23:59"))
    time = str(bar.get("time_key", ""))[11:16]
    period_start = str(bar.get("period_start") or bar.get("time_key", ""))[11:16]
    if not time:
        return True
    if bar.get("period_start"):
        return period_start >= start and time <= end if start <= end else period_start >= start or time <= end
    return start <= time <= end if start <= end else time >= start or time <= end


def _filter_session(bars: list[dict], session: str) -> list[dict]:
    return bars if session == "all" else [bar for bar in bars if _session_matches(bar, session)]


def _bandwidth(bar: dict) -> float | None:
    cached = _num(bar.get("bb_bandwidth"))
    if cached is not None:
        return cached
    upper = _num(bar.get("bb_upper"))
    lower = _num(bar.get("bb_lower"))
    mid = _num(bar.get("bb_mid"))
    if upper is None or lower is None or not mid:
        return None
    return (upper - lower) / mid


def _bb_mid_slope(bar: dict, prev: dict | None) -> float | None:
    cached = _num(bar.get("bb_mid_slope"))
    if cached is not None:
        return cached
    mid = _num(bar.get("bb_mid"))
    prev_mid = _num(prev.get("bb_mid")) if prev else None
    if mid is None or prev_mid is None:
        return None
    return mid - prev_mid


def _bb_mid_flat(bar: dict, prev: dict | None) -> bool:
    slope = _bb_mid_slope(bar, prev)
    close = _num(bar.get("close"))
    return slope is not None and close is not None and abs(slope) <= close * BB_MID_FLAT_TOLERANCE


def _body_side_ratios(bar: dict) -> tuple[float, float]:
    open_price = _num(bar.get("open"))
    close = _num(bar.get("close"))
    mid = _num(bar.get("bb_mid"))
    if open_price is None or close is None or mid is None:
        return 0.0, 0.0
    top = max(open_price, close)
    bottom = min(open_price, close)
    body = top - bottom
    if body <= 0:
        return 0.0, 0.0
    return max(0.0, top - mid) / body, max(0.0, mid - bottom) / body


def _is_choppy_around_mid(bars: list[dict], index: int, lookback: int = 8, flip_threshold: int = 3, narrow_bw_pct: float = 0.02) -> bool:
    if index < 1:
        return False
    start = max(0, index - lookback + 1)
    window = bars[start:index + 1]
    sides = []
    flats = []
    for offset, item in enumerate(window):
        mid = _num(item.get("bb_mid"))
        close = _num(item.get("close"))
        prev = bars[start + offset - 1] if start + offset > 0 else None
        if mid is None or close is None:
            continue
        sides.append(1 if close > mid else -1 if close < mid else 0)
        flats.append(_bb_mid_flat(item, prev))
    flips = sum(1 for a, b in zip(sides, sides[1:]) if a and b and a != b)
    mostly_flat = bool(flats) and sum(flats) / len(flats) >= 0.5
    width = _bandwidth(bars[index])
    prev_width = _bandwidth(bars[index - 1]) if index > 0 else None
    expanding = bool(width is not None and prev_width is not None and width > prev_width)
    tight_without_expansion = bool(width is not None and width < narrow_bw_pct and not expanding)
    return (flips >= flip_threshold and mostly_flat) or tight_without_expansion


def _cold_start_mid_signal(bar: dict, prev: dict | None, bars: list[dict], index: int) -> tuple[str | None, float, str]:
    if _is_choppy_around_mid(bars, index):
        return None, 0.0, ""
    close = _num(bar.get("close"))
    mid = _num(bar.get("bb_mid"))
    slope = _bb_mid_slope(bar, prev)
    if close is None or mid is None or slope is None or _bb_mid_flat(bar, prev):
        return None, 0.0, ""
    slope_strength = abs(slope) / max(abs(close) * 0.0005, 1e-9)
    conf = max(0.3, min(0.5, 0.35 + min(slope_strength, 0.15)))
    if close > mid and slope > 0:
        return "LONG", conf, "Trend-side long: close is above BB mid with rising midline"
    if close < mid and slope < 0:
        return "SHORT", conf, "Trend-side short: close is below BB mid with falling midline"
    return None, 0.0, ""


def _bb_mid_cross_signal(bar: dict, prev: dict | None, prev_prev: dict | None, bars: list[dict], index: int, timeframe: str) -> tuple[str | None, float, str]:
    close = _num(bar.get("close"))
    prev_close = _num(prev.get("close")) if prev else None
    mid = _num(bar.get("bb_mid"))
    prev_mid = _num(prev.get("bb_mid")) if prev else None
    if close is None or prev_close is None or mid is None or prev_mid is None:
        return None, 0.0, ""
    slope = _bb_mid_slope(bar, prev)
    prev_slope = _bb_mid_slope(prev, prev_prev) if prev else None
    if slope is None:
        return None, 0.0, ""
    flat = _bb_mid_flat(bar, prev)
    width = _bandwidth(bar)
    distance = abs(close - mid) / max(abs(mid) * max(width or 0.01, 1e-9), 1e-9)
    slope_strength = abs(slope) / max(abs(close) * 0.0005, 1e-9)
    conf = max(0.1, min(1.0, 0.5 + min(distance, 0.3) + min(slope_strength, 0.2) - (0.08 if flat else 0.0)))
    if timeframe == "1m":
        recent_slopes = [_bb_mid_slope(bars[j], bars[j - 1] if j > 0 else None) for j in range(max(1, index - 2), index + 1)]
        recent_up_turn = any(s is not None and s > 0 and (recent_slopes[pos - 1] if pos else prev_slope) is not None and (recent_slopes[pos - 1] if pos else prev_slope) <= 0 for pos, s in enumerate(recent_slopes))
        recent_down_turn = any(s is not None and s < 0 and (recent_slopes[pos - 1] if pos else prev_slope) is not None and (recent_slopes[pos - 1] if pos else prev_slope) >= 0 for pos, s in enumerate(recent_slopes))
        turned_up = slope > 0 and (recent_up_turn or prev_slope is None or prev_slope <= 0)
        turned_down = slope < 0 and (recent_down_turn or prev_slope is None or prev_slope >= 0)
        if prev_close < prev_mid and close > mid and turned_up:
            return "LONG", conf, "Closed across BB mid while BB mid slope turned upward"
        if prev_close > prev_mid and close < mid and turned_down:
            return "SHORT", conf, "Closed across BB mid while BB mid slope turned downward"
        return _cold_start_mid_signal(bar, prev, bars, index)
    body = abs((_num(bar.get("close"), 0) or 0) - (_num(bar.get("open"), 0) or 0))
    atr = _num(bar.get("atr14"))
    if atr is not None and body < atr * 0.1:
        return None, 0.0, ""
    prev_above, prev_below = _body_side_ratios(prev)
    above, below = _body_side_ratios(bar)
    if prev_below > 0.5 and close > mid and above > 0.5 and slope > 0 and not flat:
        return "LONG", conf, "Majority candle body crossed above BB mid with upward non-flat midline"
    if prev_above > 0.5 and close < mid and below > 0.5 and slope < 0 and not flat:
        return "SHORT", conf, "Majority candle body crossed below BB mid with downward non-flat midline"
    return _cold_start_mid_signal(bar, prev, bars, index)


def _avg_volume(bars: list[dict], index: int, window: int = 20) -> float | None:
    if index < window:
        return None
    values = [_num(bar.get("volume"), 0) for bar in bars[index - window:index]]
    return sum(values) / len(values) if values else None


def _signal_components(bar: dict, prev: dict | None, bars: list[dict], index: int) -> dict[str, tuple[str | None, float, str]]:
    close = _num(bar.get("close"))
    prev_close = _num(prev.get("close")) if prev else None
    bb_upper = _num(bar.get("bb_upper"))
    bb_lower = _num(bar.get("bb_lower"))
    vwap = _num(bar.get("vwap"))
    prev_vwap = _num(prev.get("vwap")) if prev else None
    macd = _num(bar.get("macd_dif"))
    macd_sig = _num(bar.get("macd_dea"))
    hist = _num(bar.get("macd_hist"))
    prev_macd = _num(prev.get("macd_dif")) if prev else None
    prev_macd_sig = _num(prev.get("macd_dea")) if prev else None
    prev_hist = _num(prev.get("macd_hist")) if prev else None
    k = _num(bar.get("stoch_rsi_k"))
    d = _num(bar.get("stoch_rsi_d"))
    prev_k = _num(prev.get("stoch_rsi_k")) if prev else None
    prev_d = _num(prev.get("stoch_rsi_d")) if prev else None
    out: dict[str, tuple[str | None, float, str]] = {
        "bb_squeeze": (None, 0.0, ""),
        "bb_breakout": (None, 0.0, ""),
        "bb_mid_cross": (None, 0.0, ""),
        "stoch_cross": (None, 0.0, ""),
        "vwap_bounce": (None, 0.0, ""),
        "macd_cross": (None, 0.0, ""),
    }
    if close is None:
        return out

    width = _bandwidth(bar)
    prior = _bandwidth(bars[index - 5]) if index >= 5 else None
    narrowing = width is not None and prior is not None and width < prior
    expanding = width is not None and prior is not None and width > prior
    if narrowing and bb_lower is not None and close <= bb_lower * 1.0015:
        conf = min(1.0, 0.55 + abs((close - bb_lower) / close) * 50)
        out["bb_squeeze"] = ("LONG", conf, "Price touched lower band while bandwidth narrowed")
    elif narrowing and bb_upper is not None and close >= bb_upper * 0.9985:
        conf = min(1.0, 0.55 + abs((close - bb_upper) / close) * 50)
        out["bb_squeeze"] = ("SHORT", conf, "Price touched upper band while bandwidth narrowed")

    bullish_momentum = (hist is not None and hist > 0 and (prev_hist is None or hist >= prev_hist)) or (
        prev_close is not None and close > prev_close
    )
    bearish_momentum = (hist is not None and hist < 0 and (prev_hist is None or hist <= prev_hist)) or (
        prev_close is not None and close < prev_close
    )
    if expanding and bb_upper is not None and close > bb_upper * 1.0005 and bullish_momentum:
        conf = min(1.0, 0.60 + abs((close - bb_upper) / close) * 60)
        out["bb_breakout"] = ("LONG", conf, "Closed above upper band while bandwidth expanded")
    elif expanding and bb_lower is not None and close < bb_lower * 0.9995 and bearish_momentum:
        conf = min(1.0, 0.60 + abs((close - bb_lower) / close) * 60)
        out["bb_breakout"] = ("SHORT", conf, "Closed below lower band while bandwidth expanded")

    threshold = 20
    if prev_k is not None and prev_d is not None and k is not None and d is not None:
        prev_pos = prev_k - prev_d
        curr_pos = k - d
        if prev_pos <= 0 < curr_pos and k <= threshold:
            conf = min(1.0, 0.55 + (threshold - min(k, threshold)) / threshold * 0.35 + abs(curr_pos) / 100)
            out["stoch_cross"] = ("LONG", conf, f"K crossed above D at {k:.2f}")
        elif prev_pos >= 0 > curr_pos and k >= 100 - threshold:
            conf = min(1.0, 0.55 + (max(k, 100 - threshold) - (100 - threshold)) / threshold * 0.35 + abs(curr_pos) / 100)
            out["stoch_cross"] = ("SHORT", conf, f"K crossed below D at {k:.2f}")

    avg_vol = _avg_volume(bars, index)
    volume_ok = avg_vol is not None and _num(bar.get("volume"), 0) >= avg_vol * 1.05
    if prev_close is not None and prev_vwap is not None and vwap is not None and volume_ok:
        vol_conf = min(1.0, 0.6 + (_num(bar.get("volume"), 0) / avg_vol - 1) * 0.2)
        if prev_close <= prev_vwap and close > vwap:
            out["vwap_bounce"] = ("LONG", vol_conf, "Price reclaimed VWAP on confirming volume")
        elif prev_close >= prev_vwap and close < vwap:
            out["vwap_bounce"] = ("SHORT", vol_conf, "Price rejected VWAP on confirming volume")

    if prev_macd is not None and prev_macd_sig is not None and macd is not None and macd_sig is not None:
        prev_pos = prev_macd - prev_macd_sig
        curr_pos = macd - macd_sig
        if prev_pos <= 0 < curr_pos and hist is not None and hist > 0:
            conf = min(1.0, 0.58 + min(abs(hist) / max(abs(close), 1) * 100, 0.35))
            out["macd_cross"] = ("LONG", conf, "MACD crossed above signal with positive histogram")
        elif prev_pos >= 0 > curr_pos and hist is not None and hist < 0:
            conf = min(1.0, 0.58 + min(abs(hist) / max(abs(close), 1) * 100, 0.35))
            out["macd_cross"] = ("SHORT", conf, "MACD crossed below signal with negative histogram")
    return out


def _fast_signal(strategy_id: str, bar: dict, prev: dict | None, bars: list[dict], index: int) -> tuple[str | None, float, str]:
    base = strategy_id.rsplit("_", 1)[0]
    if base == "bb_mid_cross":
        timeframe = strategy_id.rsplit("_", 1)[-1]
        prev_prev = bars[index - 2] if index >= 2 else None
        return _bb_mid_cross_signal(bar, prev, prev_prev, bars, index, timeframe)
    signals = _signal_components(bar, prev, bars, index)
    if base in signals:
        return signals[base]
    if base == "mean_reversion":
        bb_side, bb_conf, bb_reason = signals["bb_squeeze"]
        if bb_side is None:
            return None, 0.0, ""
        confirmations = [signals[name] for name in ["stoch_cross", "vwap_bounce", "macd_cross"] if signals[name][0] == bb_side]
        if not confirmations:
            return None, 0.0, ""
        best_side, best_conf, best_reason = max(confirmations, key=lambda item: item[1])
        return bb_side, (bb_conf * 2 + best_conf) / 3, f"{bb_reason}; confirmation: {best_reason}"
    return None, 0.0, ""


def _timeframe_bars(bars: list[dict], timeframe: str) -> list[dict]:
    minutes = _timeframe_minutes(timeframe)
    if minutes <= 1:
        return bars
    out = []
    bucket: list[dict] = []
    current_key = None
    for bar in bars:
        time_key = str(bar.get("time_key", ""))
        minute = int(time_key[14:16] or 0)
        key = f"{time_key[:14]}{minute - minute % minutes:02d}"
        if current_key is not None and key != current_key and bucket:
            out.append(_aggregate_bucket(bucket, current_key, minutes))
            bucket = []
        current_key = key
        bucket.append(bar)
    if bucket:
        out.append(_aggregate_bucket(bucket, current_key, minutes))
    return out


def _add_minutes_to_time_key(time_key: str, minutes: int) -> str:
    from datetime import datetime, timedelta

    try:
        return (datetime.strptime(time_key, "%Y-%m-%d %H:%M:%S") + timedelta(minutes=minutes)).strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return time_key


def _close_labeled_bars(bars: list[dict], minutes: int) -> list[dict]:
    out = []
    for bar in bars:
        shifted = dict(bar)
        shifted["period_start"] = str(bar.get("time_key", ""))
        time_key = _add_minutes_to_time_key(str(bar.get("time_key", "")), minutes)
        shifted["time_key"] = time_key
        shifted["date"] = str(time_key or bar.get("date") or "")[:10]
        out.append(shifted)
    return out


def _aggregate_bucket(bucket: list[dict], bucket_start_key: str | None = None, minutes: int = 1) -> dict:
    first = bucket[0]
    last = bucket[-1]
    time_key = _add_minutes_to_time_key(f"{bucket_start_key}:00", minutes) if bucket_start_key else last.get("time_key")
    merged = dict(last)
    merged.update(
        {
            "period_start": first.get("time_key"),
            "time_key": time_key,
            "date": str(time_key or last.get("time_key", ""))[:10],
            "open": first.get("open"),
            "high": max(_num(b.get("high"), 0) for b in bucket),
            "low": min(_num(b.get("low"), 0) for b in bucket),
            "close": last.get("close"),
            "volume": sum(_num(b.get("volume"), 0) for b in bucket),
        }
    )
    return merged


def _ema(values: list[float | None], span: int) -> list[float | None]:
    alpha = 2 / (span + 1)
    out: list[float | None] = []
    prev = None
    for value in values:
        if value is None:
            out.append(prev)
            continue
        prev = value if prev is None else value * alpha + prev * (1 - alpha)
        out.append(prev)
    return out


def _compute_derived_indicators(bars: list[dict]) -> list[dict]:
    if not bars:
        return bars
    out = [dict(bar) for bar in bars]
    closes = [_num(bar.get("close")) for bar in out]
    highs = [_num(bar.get("high")) for bar in out]
    lows = [_num(bar.get("low")) for bar in out]
    volumes = [_num(bar.get("volume"), 0) or 0 for bar in out]
    period = 20
    for i, bar in enumerate(out):
        window = [v for v in closes[max(0, i - period + 1):i + 1] if v is not None]
        if len(window) >= period:
            mid = sum(window) / len(window)
            variance = sum((v - mid) ** 2 for v in window) / max(1, len(window) - 1)
            std = math.sqrt(variance)
            bar["bb_mid"] = round(mid, 6)
            bar["bb_upper"] = round(mid + 2 * std, 6)
            bar["bb_lower"] = round(mid - 2 * std, 6)
            prev_mid = _num(out[i - 1].get("bb_mid")) if i else None
            slope = mid - prev_mid if prev_mid is not None else None
            bar["bb_mid_slope"] = round(slope, 6) if slope is not None else None
            bar["bb_mid_flat"] = bool(slope is not None and abs(slope) <= (closes[i] or 0) * BB_MID_FLAT_TOLERANCE)
            bar["bb_bandwidth"] = round((bar["bb_upper"] - bar["bb_lower"]) / mid, 8) if mid else None
            prev_width = _num(out[i - 1].get("bb_bandwidth")) if i else None
            bar["bb_bandwidth_expanding"] = bool(prev_width is not None and bar["bb_bandwidth"] is not None and bar["bb_bandwidth"] > prev_width)
        else:
            bar["bb_mid"] = bar.get("bb_mid")
            bar["bb_upper"] = bar.get("bb_upper")
            bar["bb_lower"] = bar.get("bb_lower")

    ema_fast = _ema(closes, 8)
    ema_slow = _ema(closes, 21)
    macd_line = [(a - b) if a is not None and b is not None else None for a, b in zip(ema_fast, ema_slow)]
    macd_sig = _ema(macd_line, 5)
    cum_pv_by_day: dict[str, float] = {}
    cum_vol_by_day: dict[str, float] = {}
    gains: list[float] = []
    losses: list[float] = []
    rsi_values: list[float | None] = []
    for i, bar in enumerate(out):
        day = str(bar.get("date") or str(bar.get("time_key", ""))[:10])
        typical = ((_num(bar.get("high"), 0) or 0) + (_num(bar.get("low"), 0) or 0) + (_num(bar.get("close"), 0) or 0)) / 3
        cum_pv_by_day[day] = cum_pv_by_day.get(day, 0.0) + typical * volumes[i]
        cum_vol_by_day[day] = cum_vol_by_day.get(day, 0.0) + volumes[i]
        bar["vwap"] = round(cum_pv_by_day[day] / cum_vol_by_day[day], 6) if cum_vol_by_day[day] else bar.get("vwap")
        bar["macd_dif"] = round(macd_line[i], 6) if macd_line[i] is not None else None
        bar["macd_dea"] = round(macd_sig[i], 6) if macd_sig[i] is not None else None
        bar["macd_hist"] = round(macd_line[i] - macd_sig[i], 6) if macd_line[i] is not None and macd_sig[i] is not None else None
        if i == 0 or closes[i] is None or closes[i - 1] is None:
            gains.append(0.0)
            losses.append(0.0)
            rsi_values.append(None)
        else:
            change = closes[i] - closes[i - 1]
            gains.append(max(0.0, change))
            losses.append(max(0.0, -change))
            if i >= 14:
                avg_gain = sum(gains[i - 13:i + 1]) / 14
                avg_loss = sum(losses[i - 13:i + 1]) / 14
                rsi = 100 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
                rsi_values.append(rsi)
            else:
                rsi_values.append(None)
        if i > 0 and highs[i] is not None and lows[i] is not None and closes[i - 1] is not None:
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
            trs = [max((highs[j] or 0) - (lows[j] or 0), abs((highs[j] or 0) - (closes[j - 1] or 0)), abs((lows[j] or 0) - (closes[j - 1] or 0))) for j in range(max(1, i - 13), i + 1)]
            bar["atr14"] = round(sum(trs) / len(trs), 6) if trs else round(tr, 6)
    for i, bar in enumerate(out):
        rsi_window = [v for v in rsi_values[max(0, i - 13):i + 1] if v is not None]
        if len(rsi_window) >= 2 and rsi_values[i] is not None:
            lo, hi = min(rsi_window), max(rsi_window)
            k = 50.0 if hi == lo else (rsi_values[i] - lo) / (hi - lo) * 100
            bar["stoch_rsi_k"] = round(k, 6)
            k_window = [_num(out[j].get("stoch_rsi_k")) for j in range(max(0, i - 2), i + 1)]
            k_window = [v for v in k_window if v is not None]
            bar["stoch_rsi_d"] = round(sum(k_window) / len(k_window), 6) if k_window else None
    return out


def _is_final_bar(bars: list[dict], index: int) -> bool:
    if index >= len(bars) - 1:
        return True
    time_key = str(bars[index].get("time_key", ""))
    next_time_key = str(bars[index + 1].get("time_key", ""))
    return str(bars[index].get("date") or time_key[:10]) != str(bars[index + 1].get("date") or next_time_key[:10])


def _fast_trades(strategy_id: str, label: str, bars: list[dict], symbol: str, start_id: int) -> list[dict]:
    position = None
    trades = []
    next_id = start_id
    prev = None
    is_mid_cross_strategy = strategy_id.startswith("bb_mid_cross_")
    cooldown = 0 if is_mid_cross_strategy else (6 if strategy_id.endswith("_1m") else 3)
    max_trades_per_day = None if is_mid_cross_strategy else (8 if strategy_id.endswith("_1m") else 4)
    last_exit_index = -10_000
    trades_by_day: dict[str, int] = {}
    for index, bar in enumerate(bars):
        side, confidence, reason = _fast_signal(strategy_id, bar, prev, bars, index)
        prev = bar
        price = _num(bar.get("close"))
        if price is None or price <= 0:
            continue
        time_key = str(bar.get("time_key"))
        day = str(bar.get("date") or time_key[:10])
        if side is None:
            continue
        is_mid_cross = strategy_id.startswith("bb_mid_cross_")
        is_cold_start = reason.startswith("Cold-start")
        if position is None:
            if is_mid_cross and time_key[11:16] < BB_MID_FIRST_ENTRY_TIME:
                continue
            if is_mid_cross and not is_cold_start and _is_choppy_around_mid(bars, index):
                continue
            if _is_final_bar(bars, index):
                continue
            if index - last_exit_index < cooldown:
                continue
            if max_trades_per_day is not None and trades_by_day.get(day, 0) >= max_trades_per_day:
                continue
            position = {
                "side": side,
                "entry": price,
                "entryTime": time_key,
                "entryIndex": index,
                "day": day,
                "shares": max(1, int(BACKTEST_NOTIONAL // price)),
                "entryReason": f"{label}: {reason}",
                "confidence": confidence,
            }
            continue
        if side == position["side"]:
            continue
        if is_mid_cross and is_cold_start:
            continue
        if index - position["entryIndex"] < cooldown:
            continue
        pnl_per_share = _pnl(position["side"], position["entry"], price)
        trades.append(
            {
                "id": next_id,
                "name": f"{symbol} {next_id}",
                "symbol": symbol,
                "day": position["day"],
                "side": position["side"],
                "entryTime": position["entryTime"],
                "exitTime": time_key,
                "entry": round(position["entry"], 6),
                "exit": round(price, 6),
                "shares": position["shares"],
                "pnl": round(pnl_per_share, 6),
                "pnlTotal": round(pnl_per_share * position["shares"], 6),
                "reason": "opposite_signal",
                "entryReason": position["entryReason"],
            }
        )
        next_id += 1
        trades_by_day[position["day"]] = trades_by_day.get(position["day"], 0) + 1
        last_exit_index = index
        if not _is_final_bar(bars, index) and (max_trades_per_day is None or trades_by_day.get(day, 0) < max_trades_per_day):
            position = {
                "side": side,
                "entry": price,
                "entryTime": time_key,
                "entryIndex": index,
                "day": day,
                "shares": max(1, int(BACKTEST_NOTIONAL // price)),
                "entryReason": f"{label}: {reason}",
                "confidence": confidence,
            }
        else:
            position = None
    return trades


def _run_fast_dashboard_strategies(bars: list[dict], symbol: str, ktype: str = "1m") -> dict:
    labels = {
        "bb_squeeze": "Bollinger Band Squeeze",
        "bb_breakout": "Bollinger Band Breakout",
        "stoch_cross": "StochRSI Cross",
        "vwap_bounce": "VWAP Bounce",
        "macd_cross": "MACD Cross",
        "bb_mid_cross": "Bollinger Midline Cross",
        "mean_reversion": "Mean Reversion",
    }
    descriptions = {
        "bb_squeeze": "Trades outer Bollinger Band touches only when band width is narrowing.",
        "bb_breakout": "Trades closes outside the Bollinger Bands when band width is expanding.",
        "stoch_cross": "Trades StochRSI K/D crosses from oversold or overbought zones.",
        "vwap_bounce": "Trades VWAP reclaim or rejection only when volume confirms the move.",
        "macd_cross": "Trades MACD line/signal crossovers confirmed by histogram direction.",
        "bb_mid_cross": "Trades BB middle-line crosses only when the midline direction confirms and anti-chop filters allow entry.",
        "mean_reversion": "Trades Bollinger Band mean reversion only when momentum confirms the band touch.",
    }
    rule_notes = {
        "bb_squeeze": "Looks for stretched price at the outer Bollinger Bands while volatility compresses. It is a mean-reversion setup, not a breakout setup.",
        "bb_breakout": "Looks for price closing outside an outer Bollinger Band while volatility expands. It follows continuation, so it is the opposite idea from BB squeeze mean reversion.",
        "stoch_cross": "Looks for StochRSI K crossing D below 20 for longs or above 80 for shorts. Signals outside those zones are ignored.",
        "vwap_bounce": "Looks for price reclaiming VWAP for longs or rejecting VWAP for shorts, with current volume at least 1.05x the recent average.",
        "macd_cross": "Looks for MACD crossing above signal with positive histogram for longs, or crossing below signal with negative histogram for shorts.",
        "bb_mid_cross": "Looks for price crossing the Bollinger middle line with BB mid slope confirmation. It blocks repeated flat midline flips and tight bandwidth unless bands are expanding.",
        "mean_reversion": "Requires a Bollinger Band squeeze signal plus same-direction confirmation from StochRSI, VWAP, or MACD.",
    }
    strategy_notes = {
        "bb_squeeze": ["Entry rules: buy near the lower Bollinger Band when bandwidth is narrowing; sell near the upper band when bandwidth is narrowing.", "Exit rules: close or reverse when the same strategy produces an opposite signal after cooldown.", "Filters: requires narrowing bandwidth and uses cooldown to avoid repeated band-touch entries.", "Best conditions: works best in ranging markets that stretch to the bands and revert.", "Weaknesses: can fail badly when a band touch becomes a real breakout."],
        "bb_breakout": ["Entry rules: buy when close breaks above the upper band with expanding bandwidth and bullish momentum; sell when close breaks below the lower band with expanding bandwidth and bearish momentum.", "Exit rules: close or reverse when an opposite breakout signal appears after cooldown.", "Filters: requires bandwidth expansion and momentum confirmation.", "Best conditions: works best when volatility expands out of compression.", "Weaknesses: false breakouts can reverse quickly back inside the bands."],
        "stoch_cross": ["Entry rules: buy when StochRSI K crosses above D in the oversold zone; sell when K crosses below D in the overbought zone.", "Exit rules: close or reverse when an opposite StochRSI zone crossover appears after cooldown.", "Filters: ignores crosses outside the oversold or overbought zones and uses cooldown.", "Best conditions: works best after sharp intraday extensions that start to mean revert.", "Weaknesses: can fire early in strong trends where overbought or oversold stays pinned."],
        "vwap_bounce": ["Entry rules: buy when price reclaims VWAP from below on confirming volume; sell when price rejects VWAP from above on confirming volume.", "Exit rules: close or reverse when the opposite VWAP reclaim or rejection appears after cooldown.", "Filters: requires current volume above the rolling average volume threshold and uses cooldown.", "Best conditions: works best when VWAP is acting as an intraday control level.", "Weaknesses: can be noisy around VWAP when volume is uneven or price is range-bound."],
        "macd_cross": ["Entry rules: buy when MACD crosses above signal with positive histogram; sell when MACD crosses below signal with negative histogram.", "Exit rules: close or reverse when the opposite MACD crossover appears after cooldown.", "Filters: requires histogram to confirm the crossover direction and uses cooldown.", "Best conditions: works best when momentum is cleanly shifting after consolidation.", "Weaknesses: lags fast reversals and can whipsaw in low-volatility chop."],
        "bb_mid_cross": ["Entry rules: when no position is open, enter long if the close is above BB mid and BB mid is sloping up; enter short if the close is below BB mid and BB mid is sloping down.", "Entry rules: when a position is open, close and reverse on the opposite BB mid cross with slope confirmation.", "Filters: do not open a new position before 09:45.", "Filters: if BB mid is flat on the current candle, wait for the next candle.", "Filters: blocks fresh entries during repeated flat midline flips and tight bandwidth unless bands are expanding; no cooldown or daily trade cap is applied.", "Best conditions: works best when price is rotating through BB mid as a new directional move starts.", "Weaknesses: can still whipsaw near the midline if slope changes are tiny and volatility has not expanded."],
        "mean_reversion": ["Entry rules: buy when a lower-band squeeze aligns with StochRSI, VWAP, or MACD confirmation; sell when the upper-band version aligns.", "Exit rules: close or reverse when the composite setup flips direction after cooldown.", "Filters: requires a Bollinger squeeze signal plus at least one same-direction momentum or VWAP confirmation.", "Best conditions: works best in ranging markets where stretched moves snap back with confirmation.", "Weaknesses: can miss simple reversals with no confirmation and can fight strong breakouts."],
    }
    entry_rules = {
        "bb_squeeze": [["Band touch", "Close is at or below the lower band while bandwidth narrows.", "Close is at or above the upper band while bandwidth narrows."]],
        "bb_breakout": [["Band breakout", "Close breaks above the upper band while bandwidth expands and momentum confirms.", "Close breaks below the lower band while bandwidth expands and momentum confirms."]],
        "stoch_cross": [["StochRSI cross", "K crosses above D while K is at or below 20.", "K crosses below D while K is at or above 80."]],
        "vwap_bounce": [["VWAP reclaim/reject", "Previous close was below VWAP, current close reclaims VWAP on volume.", "Previous close was above VWAP, current close rejects VWAP on volume."]],
        "macd_cross": [["MACD crossover", "MACD crosses above signal and histogram is positive.", "MACD crosses below signal and histogram is negative."]],
        "bb_mid_cross": [["BB midline cross", "Close crosses above BB mid with midline slope turning up on 1m, or majority body above BB mid with rising non-flat midline on higher timeframes.", "Close crosses below BB mid with midline slope turning down on 1m, or majority body below BB mid with falling non-flat midline on higher timeframes."]],
        "mean_reversion": [["Composite setup", "Lower-band squeeze plus same-direction Stoch/VWAP/MACD confirmation.", "Upper-band squeeze plus same-direction Stoch/VWAP/MACD confirmation."]],
    }
    exit_rules = {
        name: [["Opposite signal", "Close long when the same strategy produces a short signal after cooldown.", "Close short when the same strategy produces a long signal after cooldown."]]
        for name in labels
    }
    strategies = []
    next_id = 1
    loaded_minutes = _bars_interval_minutes(bars)
    for timeframe in _strategy_timeframes(ktype):
        source_bars = bars if loaded_minutes >= _timeframe_minutes(timeframe) else _timeframe_bars(bars, timeframe)
        for name in STRATEGY_NAMES:
            strategy_id = f"{name}_{timeframe}"
            label = labels[name]
            trades = _fast_trades(strategy_id, label, source_bars, symbol, next_id)
            next_id += len(trades)
            strategies.append(
                {
                    "id": strategy_id,
                    "label": label,
                    "description": descriptions[name],
                    "stats": _summarize_dashboard_trades(label, trades),
                    "trades": trades,
                    "rules": {
                        "label": label,
                        "description": descriptions[name],
                        "note": rule_notes[name],
                        "strategy_notes": strategy_notes[name],
                        "entry": entry_rules[name],
                        "exit": exit_rules[name],
                    },
                }
            )
    strategies.sort(key=lambda item: (item["stats"]["win_rate"], item["stats"]["pnl_per_share"]), reverse=True)
    return {"strategies": strategies, "defaultStrategy": strategies[0]["id"] if strategies else "bb_squeeze_1m"}


class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def _send_json(self, data, status: int = 200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/trade-data-list":
            items = []
            for symbol_dir in sorted(DATA_ROOT.iterdir() if DATA_ROOT.exists() else []):
                if not symbol_dir.is_dir():
                    continue
                for file_path in sorted(symbol_dir.glob("*.json")):
                    items.append({"symbol": f"US.{symbol_dir.name}", "file": str(file_path.name)})
            self._send_json(items)
            return
        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            self._send_json({"error": "Invalid JSON body."}, 400)
            return

        if path == "/api/bars":
            try:
                symbol = _plain_symbol(str(payload.get("symbol") or "INTC"))
                ktype = str(payload.get("ktype") or "1m")
                start = str(payload.get("start") or "")
                end = str(payload.get("end") or "")
                bars, source = _load_bars(symbol, ktype, start, end)
                days = sorted({bar["date"] for bar in bars if bar.get("date")})
                self._send_json(
                    {
                        "symbol": f"US.{symbol}",
                        "bar_interval": ktype,
                        "start": start,
                        "end": end,
                        "rows": len(bars),
                        "days": days,
                        "bars": bars,
                        "source": "trade_data",
                        "cache": {"file": str(source), "symbol": f"US.{symbol}", "ktype": ktype, "rows": len(bars)},
                    }
                )
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        if path == "/api/strategies":
            try:
                self._send_json(_run_dashboard_strategies(payload))
            except Exception as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        if path in {"/api/realtime/start", "/api/realtime/stop"}:
            self._send_json({"ok": False, "message": "Realtime stream is not enabled in dashboard_server.py."})
            return

        self._send_json({"error": f"Unknown endpoint: {path}"}, 404)


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 8000), DashboardHandler)
    print("Dashboard server running at http://127.0.0.1:8000/static/index.html", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

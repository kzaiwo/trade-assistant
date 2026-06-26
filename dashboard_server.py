from __future__ import annotations

import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from config import BACKTEST_NOTIONAL


ROOT = Path(__file__).resolve().parent
DATA_ROOT = (ROOT / "../_trade_data").resolve()
RESULTS_PATH = ROOT / "results/backtest_summary.json"
SYMBOLS = ["AAPL", "TSLA", "MU", "INTC"]
STRATEGY_NAMES = ["bb_squeeze", "bb_breakout", "stoch_cross", "vwap_bounce", "macd_cross", "mean_reversion"]
TIMEFRAMES = ["1m", "5m"]
BARS_CACHE: dict[tuple[str, str, str, str], tuple[list[dict], Path]] = {}


def _plain_symbol(value: str) -> str:
    return value.strip().upper().replace("US.", "") or "INTC"


def _timeframe_hint(ktype: str) -> str:
    return "5min" if ktype == "5m" else "1min"


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
    if not time:
        return True
    return start <= time <= end if start <= end else time >= start or time <= end


def _filter_session(bars: list[dict], session: str) -> list[dict]:
    return bars if session == "all" else [bar for bar in bars if _session_matches(bar, session)]


def _bandwidth(bar: dict) -> float | None:
    upper = _num(bar.get("bb_upper"))
    lower = _num(bar.get("bb_lower"))
    mid = _num(bar.get("bb_mid"))
    if upper is None or lower is None or not mid:
        return None
    return (upper - lower) / mid


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
    signals = _signal_components(bar, prev, bars, index)
    base = strategy_id.rsplit("_", 1)[0]
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
    if timeframe == "1m":
        return bars
    if timeframe != "5m":
        return bars
    out = []
    bucket: list[dict] = []
    current_key = None
    for bar in bars:
        time_key = str(bar.get("time_key", ""))
        minute = int(time_key[14:16] or 0)
        key = f"{time_key[:14]}{minute - minute % 5:02d}"
        if current_key is not None and key != current_key and bucket:
            out.append(_aggregate_bucket(bucket))
            bucket = []
        current_key = key
        bucket.append(bar)
    if bucket:
        out.append(_aggregate_bucket(bucket))
    return out


def _aggregate_bucket(bucket: list[dict]) -> dict:
    first = bucket[0]
    last = bucket[-1]
    merged = dict(last)
    merged.update(
        {
            "time_key": last.get("time_key"),
            "date": last.get("date") or str(last.get("time_key", ""))[:10],
            "open": first.get("open"),
            "high": max(_num(b.get("high"), 0) for b in bucket),
            "low": min(_num(b.get("low"), 0) for b in bucket),
            "close": last.get("close"),
            "volume": sum(_num(b.get("volume"), 0) for b in bucket),
        }
    )
    return merged


def _fast_trades(strategy_id: str, label: str, bars: list[dict], symbol: str, start_id: int) -> list[dict]:
    position = None
    trades = []
    next_id = start_id
    prev = None
    cooldown = 6 if strategy_id.endswith("_1m") else 3
    max_trades_per_day = 8 if strategy_id.endswith("_1m") else 4
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
        if position is None:
            if index - last_exit_index < cooldown:
                continue
            if trades_by_day.get(day, 0) >= max_trades_per_day:
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
        position = None
    return trades


def _run_fast_dashboard_strategies(bars: list[dict], symbol: str, ktype: str = "1m") -> dict:
    labels = {
        "bb_squeeze": "Bollinger Band Squeeze",
        "bb_breakout": "Bollinger Band Breakout",
        "stoch_cross": "StochRSI Cross",
        "vwap_bounce": "VWAP Bounce",
        "macd_cross": "MACD Cross",
        "mean_reversion": "Mean Reversion",
    }
    descriptions = {
        "bb_squeeze": "Trades outer Bollinger Band touches only when band width is narrowing.",
        "bb_breakout": "Trades closes outside the Bollinger Bands when band width is expanding.",
        "stoch_cross": "Trades StochRSI K/D crosses from oversold or overbought zones.",
        "vwap_bounce": "Trades VWAP reclaim or rejection only when volume confirms the move.",
        "macd_cross": "Trades MACD line/signal crossovers confirmed by histogram direction.",
        "mean_reversion": "Trades Bollinger Band mean reversion only when momentum confirms the band touch.",
    }
    rule_notes = {
        "bb_squeeze": "Looks for stretched price at the outer Bollinger Bands while volatility compresses. It is a mean-reversion setup, not a breakout setup.",
        "bb_breakout": "Looks for price closing outside an outer Bollinger Band while volatility expands. It follows continuation, so it is the opposite idea from BB squeeze mean reversion.",
        "stoch_cross": "Looks for StochRSI K crossing D below 20 for longs or above 80 for shorts. Signals outside those zones are ignored.",
        "vwap_bounce": "Looks for price reclaiming VWAP for longs or rejecting VWAP for shorts, with current volume at least 1.05x the recent average.",
        "macd_cross": "Looks for MACD crossing above signal with positive histogram for longs, or crossing below signal with negative histogram for shorts.",
        "mean_reversion": "Requires a Bollinger Band squeeze signal plus same-direction confirmation from StochRSI, VWAP, or MACD.",
    }
    entry_rules = {
        "bb_squeeze": [["Band touch", "Close is at or below the lower band while bandwidth narrows.", "Close is at or above the upper band while bandwidth narrows."]],
        "bb_breakout": [["Band breakout", "Close breaks above the upper band while bandwidth expands and momentum confirms.", "Close breaks below the lower band while bandwidth expands and momentum confirms."]],
        "stoch_cross": [["StochRSI cross", "K crosses above D while K is at or below 20.", "K crosses below D while K is at or above 80."]],
        "vwap_bounce": [["VWAP reclaim/reject", "Previous close was below VWAP, current close reclaims VWAP on volume.", "Previous close was above VWAP, current close rejects VWAP on volume."]],
        "macd_cross": [["MACD crossover", "MACD crosses above signal and histogram is positive.", "MACD crosses below signal and histogram is negative."]],
        "mean_reversion": [["Composite setup", "Lower-band squeeze plus same-direction Stoch/VWAP/MACD confirmation.", "Upper-band squeeze plus same-direction Stoch/VWAP/MACD confirmation."]],
    }
    exit_rules = {
        name: [["Opposite signal", "Close long when the same strategy produces a short signal after cooldown.", "Close short when the same strategy produces a long signal after cooldown."]]
        for name in labels
    }
    strategies = []
    next_id = 1
    for timeframe in _strategy_timeframes(ktype):
        source_bars = _timeframe_bars(bars, timeframe)
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

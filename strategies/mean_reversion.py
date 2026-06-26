from signals.bb_squeeze import BBSqueeze
from signals.macd_cross import MACDCross
from signals.stoch_cross import StochCross
from signals.vwap_bounce import VWAPBounce
from strategies.base import And, Or, Strategy


class SignalStrategy(Strategy):
    def __init__(self, signal):
        self.signal = signal
        self.name = f"{signal.name}_{signal.timeframe}"
        self.display_name = f"{signal.display_name} ({signal.timeframe})"
        self.description = signal.description
        self.strategy_notes = getattr(signal, "strategy_notes", [])
        self.rule = signal
        self.valid_contexts = None


class MeanReversion(Strategy):
    description = "Looks for oversold bounces confirmed by momentum and trend shifts."
    strategy_notes = [
        "Entry rules: buy when a lower-band squeeze aligns with StochRSI, VWAP, or MACD confirmation; sell when the upper-band version aligns.",
        "Exit rules: close or reverse when the composite setup flips direction after cooldown.",
        "Filters: requires a Bollinger squeeze signal plus at least one same-direction momentum or VWAP confirmation.",
        "Best conditions: works best in ranging markets where stretched moves snap back with confirmation.",
        "Weaknesses: can miss simple reversals with no confirmation and can fight strong breakouts.",
    ]

    def __init__(self, timeframe: str = "1m"):
        self.timeframe = timeframe
        self.name = f"mean_reversion_{timeframe}"
        self.display_name = f"Mean Reversion ({timeframe})"
        self.valid_contexts = ["ranging"]
        self.rule = And(
            [
                BBSqueeze(timeframe=timeframe, weight=2.0),
                Or(
                    [
                        StochCross(timeframe=timeframe, weight=1.0, threshold=20),
                        VWAPBounce(timeframe=timeframe, weight=1.5),
                        MACDCross(timeframe=timeframe, weight=1.0),
                    ]
                ),
            ]
        )

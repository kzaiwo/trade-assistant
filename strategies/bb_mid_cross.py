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

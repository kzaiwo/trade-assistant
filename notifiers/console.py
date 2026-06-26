from models.types import SignalDirection, StrategyResult
from notifiers.base import Notifier


class ConsoleNotifier(Notifier):
    def send(self, result: StrategyResult, symbol: str):
        color = "\033[92m" if result.direction == SignalDirection.BUY else "\033[91m"
        reset = "\033[0m"
        print(f"{color}{symbol} {result.direction.value.upper()} confidence={result.confidence:.2f}{reset}")

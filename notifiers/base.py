from abc import ABC, abstractmethod

from models.types import StrategyResult


class Notifier(ABC):
    @abstractmethod
    def send(self, result: StrategyResult, symbol: str): ...

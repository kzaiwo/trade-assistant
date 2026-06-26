from abc import ABC, abstractmethod

from data.base import DataSource
from journal.logger import TradeJournal
from notifiers.base import Notifier
from strategies.base import Strategy


class Runner(ABC):
    def __init__(
        self,
        data_source: DataSource,
        strategy: Strategy,
        notifiers: list[Notifier] | None = None,
        journal: TradeJournal | None = None,
    ):
        self.data_source = data_source
        self.strategy = strategy
        self.notifiers = notifiers or []
        self.journal = journal

    @abstractmethod
    def run(self, symbols: list[str]): ...

from __future__ import annotations

from signals.base import Signal

SIGNAL_REGISTRY: dict[str, type[Signal]] = {}


def register(cls):
    SIGNAL_REGISTRY[cls.name] = cls
    return cls

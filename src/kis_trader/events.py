from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class CompletedBar:
    symbol: str
    started_at: datetime
    ended_at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_quantity: int | None = None
    ask_quantity: int | None = None
    received_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.ended_at <= self.started_at:
            raise ValueError("bar end must be after start")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError("OHLC prices must be positive")
        if self.volume < 0:
            raise ValueError("volume cannot be negative")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("invalid OHLC range")

    @property
    def event_id(self) -> str:
        raw = f"bar|{self.symbol}|{self.started_at.isoformat()}|{self.ended_at.isoformat()}"
        return hashlib.sha256(raw.encode()).hexdigest()

    @property
    def data_delay_seconds(self) -> Decimal:
        received = self.received_at or self.ended_at
        return Decimal(str(max(0.0, (received - self.ended_at).total_seconds())))


@dataclass(frozen=True)
class Signal:
    signal_id: str
    strategy_id: str
    strategy_version: str
    event_id: str
    symbol: str
    side: str
    created_at: datetime
    reason: str


def make_signal(
    strategy_id: str,
    strategy_version: str,
    event: CompletedBar,
    side: str,
    reason: str,
) -> Signal:
    raw = f"{strategy_id}|{strategy_version}|{event.event_id}|{event.symbol}|{side}"
    return Signal(
        hashlib.sha256(raw.encode()).hexdigest(),
        strategy_id,
        strategy_version,
        event.event_id,
        event.symbol,
        side,
        event.ended_at,
        reason,
    )

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time
from decimal import Decimal
from typing import Protocol

from .events import CompletedBar, Signal, make_signal


class PositionView(Protocol):
    quantity: int
    average_price: Decimal


class Strategy(Protocol):
    strategy_id: str
    version: str

    def on_bar(self, bar: CompletedBar, position: PositionView | None) -> list[Signal]: ...


def _time(value: str) -> time:
    return time.fromisoformat(value)


@dataclass
class OrbStrategy:
    strategy_id: str = "orb"
    version: str = "1.0.0"
    range_start: time = time(9, 0)
    range_end: time = time(9, 30)
    entry_end: time = time(14, 30)
    exit_time: time = time(15, 15)
    breakout_buffer_bps: Decimal = Decimal("5")
    stop_loss_pct: Decimal = Decimal("0.01")
    take_profit_pct: Decimal = Decimal("0.02")
    _ranges: dict[tuple[object, str], tuple[Decimal, Decimal]] = field(default_factory=dict)

    def on_bar(self, bar: CompletedBar, position: PositionView | None) -> list[Signal]:
        key = (bar.ended_at.date(), bar.symbol)
        close_time = bar.ended_at.time()
        if self.range_start < close_time <= self.range_end:
            high, low = self._ranges.get(key, (bar.high, bar.low))
            self._ranges[key] = (max(high, bar.high), min(low, bar.low))
            return []
        if position and position.quantity > 0:
            change = bar.close / position.average_price - Decimal(1)
            if close_time >= self.exit_time:
                return [make_signal(self.strategy_id, self.version, bar, "sell", "time_exit")]
            if change <= -self.stop_loss_pct:
                return [make_signal(self.strategy_id, self.version, bar, "sell", "stop_loss")]
            if change >= self.take_profit_pct:
                return [make_signal(self.strategy_id, self.version, bar, "sell", "take_profit")]
            return []
        opening_range = self._ranges.get(key)
        if not opening_range or close_time <= self.range_end or close_time > self.entry_end:
            return []
        threshold = opening_range[0] * (
            Decimal(1) + self.breakout_buffer_bps / Decimal(10_000)
        )
        if bar.close > threshold:
            return [make_signal(self.strategy_id, self.version, bar, "buy", "range_breakout")]
        return []


@dataclass
class VwapPullbackStrategy:
    strategy_id: str = "vwap_pullback"
    version: str = "1.0.0"
    entry_start: time = time(9, 30)
    entry_end: time = time(14, 30)
    exit_time: time = time(15, 15)
    pullback_tolerance_bps: Decimal = Decimal("15")
    min_trend_bps: Decimal = Decimal("10")
    stop_loss_pct: Decimal = Decimal("0.008")
    take_profit_pct: Decimal = Decimal("0.016")
    _totals: dict[tuple[object, str], tuple[Decimal, int]] = field(default_factory=dict)
    _previous_close: dict[tuple[object, str], Decimal] = field(default_factory=dict)

    def on_bar(self, bar: CompletedBar, position: PositionView | None) -> list[Signal]:
        key = (bar.ended_at.date(), bar.symbol)
        typical = (bar.high + bar.low + bar.close) / Decimal(3)
        value, volume = self._totals.get(key, (Decimal(0), 0))
        value += typical * bar.volume
        volume += bar.volume
        self._totals[key] = (value, volume)
        previous_close = self._previous_close.get(key)
        self._previous_close[key] = bar.close
        if volume <= 0:
            return []
        vwap = value / volume
        close_time = bar.ended_at.time()
        if position and position.quantity > 0:
            change = bar.close / position.average_price - Decimal(1)
            if close_time >= self.exit_time:
                return [make_signal(self.strategy_id, self.version, bar, "sell", "time_exit")]
            if change <= -self.stop_loss_pct:
                return [make_signal(self.strategy_id, self.version, bar, "sell", "stop_loss")]
            if change >= self.take_profit_pct:
                return [make_signal(self.strategy_id, self.version, bar, "sell", "take_profit")]
            return []
        if not (self.entry_start <= close_time <= self.entry_end) or previous_close is None:
            return []
        tolerance = vwap * self.pullback_tolerance_bps / Decimal(10_000)
        trend_floor = vwap * (Decimal(1) + self.min_trend_bps / Decimal(10_000))
        recovered = bar.low <= vwap + tolerance and bar.close > vwap and bar.close > previous_close
        if recovered and bar.high >= trend_floor:
            return [make_signal(self.strategy_id, self.version, bar, "buy", "vwap_pullback")]
        return []


def strategies_from_config(raw: dict) -> list[Strategy]:
    result: list[Strategy] = []
    orb = raw.get("orb", {})
    if orb.get("enabled", True):
        result.append(
            OrbStrategy(
                strategy_id=str(orb.get("strategy_id", "orb")),
                version=str(orb.get("version", "1.0.0")),
                range_start=_time(orb.get("range_start", "09:00")),
                range_end=_time(orb.get("range_end", "09:30")),
                entry_end=_time(orb.get("entry_end", "14:30")),
                exit_time=_time(orb.get("exit_time", "15:15")),
                breakout_buffer_bps=Decimal(str(orb.get("breakout_buffer_bps", 5))),
                stop_loss_pct=Decimal(str(orb.get("stop_loss_pct", 0.01))),
                take_profit_pct=Decimal(str(orb.get("take_profit_pct", 0.02))),
            )
        )
    vwap = raw.get("vwap_pullback", {})
    if vwap.get("enabled", True):
        result.append(
            VwapPullbackStrategy(
                strategy_id=str(vwap.get("strategy_id", "vwap_pullback")),
                version=str(vwap.get("version", "1.0.0")),
                entry_start=_time(vwap.get("entry_start", "09:30")),
                entry_end=_time(vwap.get("entry_end", "14:30")),
                exit_time=_time(vwap.get("exit_time", "15:15")),
                pullback_tolerance_bps=Decimal(str(vwap.get("pullback_tolerance_bps", 15))),
                min_trend_bps=Decimal(str(vwap.get("min_trend_bps", 10))),
                stop_loss_pct=Decimal(str(vwap.get("stop_loss_pct", 0.008))),
                take_profit_pct=Decimal(str(vwap.get("take_profit_pct", 0.016))),
            )
        )
    return result

from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .events import CompletedBar
from .simulation import SimulationEngine


def replay_csv(path: str | Path, engine: SimulationEngine) -> int:
    """Replay rows in timestamp order through the same live event entry point."""
    bars: list[CompletedBar] = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            bars.append(
                CompletedBar(
                    symbol=row["symbol"],
                    started_at=datetime.fromisoformat(row["started_at"]),
                    ended_at=datetime.fromisoformat(row["ended_at"]),
                    open=Decimal(row["open"]),
                    high=Decimal(row["high"]),
                    low=Decimal(row["low"]),
                    close=Decimal(row["close"]),
                    volume=int(row["volume"]),
                    bid=Decimal(row["bid"]) if row.get("bid") else None,
                    ask=Decimal(row["ask"]) if row.get("ask") else None,
                    bid_quantity=int(row["bid_quantity"]) if row.get("bid_quantity") else None,
                    ask_quantity=int(row["ask_quantity"]) if row.get("ask_quantity") else None,
                    received_at=datetime.fromisoformat(row["received_at"])
                    if row.get("received_at")
                    else datetime.fromisoformat(row["ended_at"]),
                )
            )
    processed = 0
    for bar in sorted(bars, key=lambda item: (item.ended_at, item.symbol)):
        processed += int(engine.on_bar(bar))
    return processed

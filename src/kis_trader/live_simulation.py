from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from .events import CompletedBar
from .market_data import MarketData
from .simulation import SimulationEngine

KST = ZoneInfo("Asia/Seoul")


class KisLiveSimulationFeed:
    """Converts KIS minute bars and quotes into completed simulation events."""

    def __init__(self, market_data: MarketData, engine: SimulationEngine):
        self.market_data = market_data
        self.engine = engine

    def poll(self, symbols: list[str], now: datetime | None = None) -> int:
        received_at = now or datetime.now(KST)
        current_minute = received_at.replace(second=0, microsecond=0)
        processed = 0
        for symbol in symbols:
            quote = self.market_data.orderbook(symbol)
            rows = self.market_data.intraday_minutes(symbol, before=received_at.time())
            events: list[CompletedBar] = []
            for row in rows:
                event = self._to_completed_bar(symbol, row, {}, received_at)
                if event and event.ended_at == current_minute:
                    event = self._to_completed_bar(symbol, row, quote, received_at)
                if event and event.ended_at <= current_minute:
                    events.append(event)
            for event in sorted(events, key=lambda item: item.ended_at):
                processed += int(self.engine.on_bar(event))
        return processed

    @staticmethod
    def _to_completed_bar(
        symbol: str, row: dict, quote: dict, received_at: datetime
    ) -> CompletedBar | None:
        hour = str(row.get("stck_cntg_hour", ""))
        if len(hour) != 6 or not hour.isdigit():
            return None
        date_value = str(row.get("stck_bsop_date", received_at.strftime("%Y%m%d")))
        try:
            started_at = datetime.strptime(date_value + hour, "%Y%m%d%H%M%S").replace(tzinfo=KST)
            ended_at = started_at + timedelta(minutes=1)
            close = Decimal(str(row.get("stck_prpr", "0")))
            return CompletedBar(
                symbol=symbol,
                started_at=started_at,
                ended_at=ended_at,
                open=Decimal(str(row.get("stck_oprc", close))),
                high=Decimal(str(row.get("stck_hgpr", close))),
                low=Decimal(str(row.get("stck_lwpr", close))),
                close=close,
                volume=int(row.get("cntg_vol", row.get("acml_vol", 0))),
                bid=Decimal(str(quote["bidp1"])) if quote.get("bidp1") else None,
                ask=Decimal(str(quote["askp1"])) if quote.get("askp1") else None,
                bid_quantity=int(quote["bidp_rsqn1"]) if quote.get("bidp_rsqn1") else None,
                ask_quantity=int(quote["askp_rsqn1"]) if quote.get("askp_rsqn1") else None,
                received_at=received_at,
            )
        except (TypeError, ValueError):
            return None

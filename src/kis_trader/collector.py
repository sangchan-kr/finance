from __future__ import annotations

import json
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Settings
from .kis_client import KisClient
from .ledger import Ledger
from .live_simulation import KisLiveSimulationFeed
from .market_data import MarketData
from .simulation import SimulationEngine

SEOUL = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class Snapshot:
    collected_at: str
    symbol: str
    price: str
    change_rate: str
    volume: str


class BackgroundCollector:
    def __init__(
        self,
        settings: Settings,
        ledger: Ledger,
        symbols: Sequence[str],
        interval_seconds: int = 60,
        on_snapshot: Callable[[Snapshot], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        client_factory: Callable[[Settings], KisClient] = KisClient,
        simulation_engine: SimulationEngine | None = None,
    ):
        if not symbols:
            raise ValueError("at least one symbol is required")
        if interval_seconds < 5:
            raise ValueError("interval must be at least 5 seconds")
        self.settings = settings
        self.ledger = ledger
        self.symbols = tuple(symbols)
        self.interval_seconds = interval_seconds
        self.on_snapshot = on_snapshot or (lambda _: None)
        self.on_status = on_status or (lambda _: None)
        self.client_factory = client_factory
        self.simulation_engine = simulation_engine
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="kis-market-collector", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def collect_once(self) -> list[Snapshot]:
        snapshots: list[Snapshot] = []
        with self.client_factory(self.settings) as client:
            market = MarketData(client)
            for symbol in self.symbols:
                if self._stop_event.is_set():
                    break
                output = market.current_price(symbol)
                collected_at = datetime.now(SEOUL).isoformat(timespec="seconds")
                snapshot = Snapshot(
                    collected_at=collected_at,
                    symbol=symbol,
                    price=str(output.get("stck_prpr", "")),
                    change_rate=str(output.get("prdy_ctrt", "")),
                    volume=str(output.get("acml_vol", "")),
                )
                if not snapshot.price:
                    raise ValueError(f"KIS response contains no price for {symbol}")
                self.ledger.save_market_snapshot(
                    collected_at=collected_at,
                    environment=self.settings.market_data_environment,
                    symbol=symbol,
                    price=snapshot.price,
                    change_rate=snapshot.change_rate,
                    volume=snapshot.volume,
                    raw_json=json.dumps(output, ensure_ascii=False, separators=(",", ":")),
                )
                snapshots.append(snapshot)
                self.on_snapshot(snapshot)
            if self.simulation_engine is not None:
                processed = KisLiveSimulationFeed(market, self.simulation_engine).poll(
                    list(self.symbols)
                )
                self.on_status(f"완성 분봉 {processed}건 가상매매 처리")
        return snapshots

    def _run(self) -> None:
        self.on_status("수집 시작")
        while not self._stop_event.is_set():
            try:
                count = len(self.collect_once())
                self.on_status(f"시세 {count}건 저장 완료")
            except Exception as exc:
                self.on_status(f"수집 오류: {exc}")
            self._stop_event.wait(self.interval_seconds)
        self.on_status("수집 중지")

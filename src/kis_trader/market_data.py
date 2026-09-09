from __future__ import annotations

from datetime import date, time
from typing import Any

from .kis_client import KisClient


class MarketData:
    def __init__(self, client: KisClient):
        self.client = client

    @staticmethod
    def _symbol(symbol: str) -> str:
        if not symbol.isdigit() or len(symbol) != 6:
            raise ValueError("symbol must contain exactly 6 digits")
        return symbol

    def current_price(self, symbol: str, market: str = "J") -> dict[str, Any]:
        body = self.client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": self._symbol(symbol)},
        )
        return body["output"]

    def intraday_minutes(
        self, symbol: str, before: time = time(15, 30), market: str = "J"
    ) -> list[dict[str, Any]]:
        body = self.client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
            "FHKST03010200",
            {
                "FID_ETC_CLS_CODE": "",
                "FID_COND_MRKT_DIV_CODE": market,
                "FID_INPUT_ISCD": self._symbol(symbol),
                "FID_INPUT_HOUR_1": before.strftime("%H%M%S"),
                "FID_PW_DATA_INCU_YN": "Y",
            },
        )
        return body.get("output2", [])

    def historical_minutes(
        self, symbol: str, trading_date: date, before: time = time(15, 30), market: str = "J"
    ) -> list[dict[str, Any]]:
        body = self.client.get(
            "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice",
            "FHKST03010230",
            {
                "FID_COND_MRKT_DIV_CODE": market,
                "FID_INPUT_ISCD": self._symbol(symbol),
                "FID_INPUT_HOUR_1": before.strftime("%H%M%S"),
                "FID_INPUT_DATE_1": trading_date.strftime("%Y%m%d"),
                "FID_PW_DATA_INCU_YN": "Y",
                "FID_FAKE_TICK_INCU_YN": "",
            },
        )
        return body.get("output2", [])

    def holidays(self, from_date: date) -> list[dict[str, Any]]:
        body = self.client.get(
            "/uapi/domestic-stock/v1/quotations/chk-holiday",
            "CTCA0903R",
            {
                "BASS_DT": from_date.strftime("%Y%m%d"),
                "CTX_AREA_NK": "",
                "CTX_AREA_FK": "",
            },
        )
        return body.get("output", [])


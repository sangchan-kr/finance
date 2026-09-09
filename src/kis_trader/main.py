from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_settings
from .kis_client import KisClient
from .ledger import Ledger
from .market_data import MarketData


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KIS market-data-only trader foundation")
    parser.add_argument("--config", default="config/settings.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    price = sub.add_parser("price")
    price.add_argument("symbol")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = load_settings(Path(args.config))
    if args.command == "init-db":
        Ledger(settings.storage.database_path).initialize()
        print(f"Initialized ledger: {settings.storage.database_path}")
        return
    if args.command == "price":
        with KisClient(settings) as client:
            output = MarketData(client).current_price(args.symbol)
        print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()


from __future__ import annotations

import argparse
import csv
import json
from decimal import Decimal
from pathlib import Path

from .config import load_settings
from .kis_client import KisClient
from .ledger import Ledger
from .live_simulation import KisLiveSimulationFeed
from .market_data import MarketData
from .replay import replay_csv
from .reporting import build_comparison_chart, strategy_metrics
from .secrets import SecretStore
from .simulation import build_simulation_engine


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KIS market-data-only trader foundation")
    parser.add_argument("--config", default="config/settings.yaml")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    price = sub.add_parser("price")
    price.add_argument("symbol")
    replay = sub.add_parser("replay", help="replay minute-bar CSV through virtual trading")
    replay.add_argument("csv_path")
    paper = sub.add_parser("paper-once", help="process latest completed KIS minute bars")
    paper.add_argument("symbols", nargs="*")
    report = sub.add_parser("report", help="build virtual-strategy performance report")
    report.add_argument("--kospi100-csv", help="optional CSV with date,close columns")
    report.add_argument("--output", default="data/exports/strategy_comparison.png")
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
        return
    ledger = Ledger(settings.storage.database_path)
    ledger.initialize()
    engine = build_simulation_engine(settings, ledger)
    if args.command == "replay":
        print(f"Processed bars: {replay_csv(args.csv_path, engine)}")
        return
    if args.command == "paper-once":
        SecretStore().apply_to_environment(settings)
        symbols = args.symbols or list(settings.collection.symbols)
        with KisClient(settings) as client:
            count = KisLiveSimulationFeed(MarketData(client), engine).poll(symbols)
        print(f"Processed completed bars: {count}")
        return
    if args.command == "report":
        strategies = [(item.strategy_id, item.version) for item in engine.strategies]
        benchmark = None
        if args.kospi100_csv:
            with Path(args.kospi100_csv).open("r", encoding="utf-8-sig", newline="") as handle:
                benchmark = [
                    (row["date"], Decimal(row["close"])) for row in csv.DictReader(handle)
                ]
        output = build_comparison_chart(ledger, strategies, args.output, benchmark)
        for strategy_id, version in strategies:
            print(strategy_metrics(ledger, strategy_id, version))
        print(f"Report: {output}")


if __name__ == "__main__":
    main()

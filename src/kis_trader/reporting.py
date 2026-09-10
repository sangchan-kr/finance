from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from matplotlib.figure import Figure

from .ledger import Ledger


@dataclass(frozen=True)
class StrategyMetrics:
    strategy_id: str
    strategy_version: str
    net_return: Decimal
    max_drawdown: Decimal
    trades: int
    win_rate: Decimal


@dataclass(frozen=True)
class DailyStrategyReturn:
    trade_date: str
    net_return: Decimal
    cumulative_index: Decimal


def daily_returns(
    ledger: Ledger, strategy_id: str, version: str
) -> list[DailyStrategyReturn]:
    with ledger.connect() as connection:
        rows = list(
            connection.execute(
                """SELECT substr(measured_at,1,10) trade_date,equity FROM strategy_equity
                WHERE strategy_id=? AND strategy_version=? ORDER BY measured_at,event_id""",
                (strategy_id, version),
            )
        )
    closes: dict[str, Decimal] = {}
    for row in rows:
        closes[row["trade_date"]] = Decimal(row["equity"])
    result: list[DailyStrategyReturn] = []
    previous: Decimal | None = None
    cumulative = Decimal(100)
    for trade_date, equity in closes.items():
        net = Decimal(0) if previous is None else equity / previous - Decimal(1)
        cumulative *= Decimal(1) + net
        result.append(DailyStrategyReturn(trade_date, net, cumulative))
        previous = equity
    return result


def strategy_metrics(ledger: Ledger, strategy_id: str, version: str) -> StrategyMetrics:
    with ledger.connect() as connection:
        equity_rows = list(
            connection.execute(
                """SELECT equity FROM strategy_equity WHERE strategy_id=? AND strategy_version=?
                ORDER BY measured_at,event_id""",
                (strategy_id, version),
            )
        )
        account = connection.execute(
            "SELECT starting_cash FROM virtual_accounts WHERE strategy_id=? AND strategy_version=?",
            (strategy_id, version),
        ).fetchone()
        fills = list(
            connection.execute(
                """SELECT side,price,quantity,commission,tax FROM virtual_fills
                WHERE strategy_id=? AND strategy_version=? ORDER BY filled_at,fill_id""",
                (strategy_id, version),
            )
        )
    if not account or not equity_rows:
        return StrategyMetrics(strategy_id, version, Decimal(0), Decimal(0), 0, Decimal(0))
    equities = [Decimal(row["equity"]) for row in equity_rows]
    peak = equities[0]
    max_drawdown = Decimal(0)
    for equity in equities:
        peak = max(peak, equity)
        if peak:
            max_drawdown = min(max_drawdown, equity / peak - Decimal(1))
    start = Decimal(account["starting_cash"])
    net_return = equities[-1] / start - Decimal(1)
    quantity = 0
    average_cost = Decimal(0)
    wins = 0
    trades = 0
    for row in fills:
        fill_quantity = int(row["quantity"])
        price = Decimal(row["price"])
        commission = Decimal(row["commission"])
        tax = Decimal(row["tax"])
        if row["side"] == "buy":
            average_cost = (
                average_cost * quantity + price * fill_quantity + commission
            ) / (quantity + fill_quantity)
            quantity += fill_quantity
        else:
            realized = (price - average_cost) * fill_quantity - commission - tax
            wins += int(realized > 0)
            trades += 1
            quantity -= fill_quantity
            if quantity == 0:
                average_cost = Decimal(0)
    return StrategyMetrics(
        strategy_id,
        version,
        net_return,
        max_drawdown,
        trades,
        Decimal(wins) / trades if trades else Decimal(0),
    )


def build_comparison_chart(
    ledger: Ledger,
    strategies: list[tuple[str, str]],
    output_path: str | Path,
    kospi100: list[tuple[object, Decimal]] | None = None,
) -> Path:
    figure = Figure(figsize=(10, 5), dpi=120)
    axis = figure.add_subplot(111)
    with ledger.connect() as connection:
        for strategy_id, version in strategies:
            rows = list(
                connection.execute(
                    """SELECT measured_at,equity FROM strategy_equity
                    WHERE strategy_id=? AND strategy_version=? ORDER BY measured_at,event_id""",
                    (strategy_id, version),
                )
            )
            if rows:
                base = Decimal(rows[0]["equity"])
                axis.plot(
                    [row["measured_at"] for row in rows],
                    [float(Decimal(row["equity"]) / base * 100) for row in rows],
                    label=f"{strategy_id} {version}",
                )
    if kospi100:
        base = kospi100[0][1]
        axis.plot([x[0] for x in kospi100], [float(x[1] / base * 100) for x in kospi100], label="KOSPI 100")
    axis.set_ylabel("Cumulative index (start=100)")
    axis.grid(True, alpha=0.25)
    handles, labels = axis.get_legend_handles_labels()
    if handles:
        axis.legend(handles, labels)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, bbox_inches="tight")
    return output

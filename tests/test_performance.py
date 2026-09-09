from decimal import Decimal

from kis_trader.performance import cumulative_index, equal_weight_return, position_return


def test_handoff_returns_and_compounding():
    returns = [
        position_return(Decimal("270000"), Decimal("269500")),
        position_return(Decimal("1401000"), Decimal("1404000")),
        position_return(Decimal("87700"), Decimal("88100")),
    ]
    daily = equal_weight_return(returns)
    assert daily.quantize(Decimal("0.0000001")) == Decimal("0.0016168")
    assert cumulative_index(Decimal("100"), daily).quantize(Decimal("0.0001")) == Decimal(
        "100.1617"
    )


def test_zero_buy_price_is_rejected():
    try:
        position_return(Decimal("0"), Decimal("1"))
    except ValueError as exc:
        assert "positive" in str(exc)
    else:
        raise AssertionError("expected ValueError")

"""Deterministic tests for A-share execution and settlement rules."""

from types import SimpleNamespace

import pandas as pd

from src.quant.broker import ExecutionBroker, Order
from src.quant.config import CostConfig, PortfolioConfig


def make_bar(**overrides):
    values = {
        "datetime": pd.Timestamp("2026-01-05 10:00:00"),
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.0,
        "volume": 1_000_000,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_order(side="buy", quantity=1000):
    return Order(side, quantity, "test", "test", pd.Timestamp("2026-01-05 09:55:00"))


def test_bought_shares_are_not_sellable_until_next_day():
    broker = ExecutionBroker(PortfolioConfig(), CostConfig(slippage_bps=0), 1_000_000)
    fill = broker.execute(make_order("buy", 1000), make_bar())

    assert fill.quantity == 1000
    assert broker.ledger.total_shares == 1000
    assert broker.ledger.sellable_shares == 0
    rejected = broker.execute(make_order("sell", 1000), make_bar())
    assert rejected.status == "rejected"

    broker.ledger.settle_new_day()
    sold = broker.execute(make_order("sell", 1000), make_bar(datetime=pd.Timestamp("2026-01-06 09:35:00")))
    assert sold.quantity == 1000
    assert broker.ledger.total_shares == 0


def test_sell_costs_include_commission_stamp_tax_and_transfer_fee():
    costs = CostConfig(
        commission_rate=0.00025,
        minimum_commission=5,
        stamp_tax_rate=0.0005,
        transfer_fee_rate=0.00001,
        slippage_bps=0,
    )
    broker = ExecutionBroker(PortfolioConfig(), costs, 1_000_000)
    broker.ledger.total_shares = 1000
    broker.ledger.sellable_shares = 1000
    fill = broker.execute(make_order("sell", 1000), make_bar())

    assert fill.gross_value == 100_000
    assert fill.commission == 25
    assert fill.stamp_tax == 50
    assert fill.transfer_fee == 1
    assert fill.cash_flow == 99_924


def test_locked_limit_and_volume_cap_reject_or_partially_fill():
    broker = ExecutionBroker(PortfolioConfig(max_volume_participation=0.05), CostConfig(slippage_bps=0), 1_000_000)
    locked = broker.execute(make_order("buy", 1000), make_bar(open=120, high=120, low=120), up_limit=120)
    assert locked.status == "rejected"
    assert locked.message == "locked_at_upper_limit"

    partial = broker.execute(make_order("buy", 1000), make_bar(volume=5_000))
    assert partial.quantity == 200


def test_dividend_adjusts_cash_and_round_lot_stock_bonus():
    broker = ExecutionBroker(PortfolioConfig(lot_size=100), CostConfig(), 1000)
    broker.ledger.total_shares = 1500
    broker.ledger.sellable_shares = 1500
    result = broker.ledger.apply_dividend(cash_per_share=0.2, stock_ratio=0.1, lot_size=100)

    assert result == {"cash_credit": 300.0, "bonus_shares": 100.0}
    assert broker.ledger.cash == 1300
    assert broker.ledger.total_shares == 1600

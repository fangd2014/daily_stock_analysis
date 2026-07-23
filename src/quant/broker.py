"""A-share T+1 portfolio ledger and conservative minute-bar execution model."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Dict, Literal, Optional

import pandas as pd

from .config import CostConfig, PortfolioConfig

Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class Order:
    side: Side
    quantity: int
    reason: str
    action: str
    signal_time: pd.Timestamp


@dataclass
class Fill:
    timestamp: pd.Timestamp
    signal_time: pd.Timestamp
    side: Side
    quantity: int
    price: float
    gross_value: float
    commission: float
    stamp_tax: float
    transfer_fee: float
    total_fees: float
    cash_flow: float
    reason: str
    action: str
    status: str = "filled"
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        values = asdict(self)
        values["timestamp"] = self.timestamp.isoformat()
        values["signal_time"] = self.signal_time.isoformat()
        return values


@dataclass
class PortfolioLedger:
    cash: float
    total_shares: int = 0
    sellable_shares: int = 0

    def settle_new_day(self) -> None:
        """Make all shares held overnight eligible for sale."""
        self.sellable_shares = self.total_shares

    def apply_dividend(self, cash_per_share: float, stock_ratio: float, lot_size: int) -> Dict[str, float]:
        """Apply cash and stock distributions before the ex-date session."""
        previous_shares = self.total_shares
        cash_credit = previous_shares * max(cash_per_share, 0.0)
        bonus_shares = int(math.floor(previous_shares * max(stock_ratio, 0.0) / lot_size) * lot_size)
        self.cash += cash_credit
        self.total_shares += bonus_shares
        self.sellable_shares += bonus_shares
        return {"cash_credit": cash_credit, "bonus_shares": float(bonus_shares)}

    def equity(self, mark_price: float) -> float:
        return self.cash + self.total_shares * mark_price


class ExecutionBroker:
    """Execute orders at the next bar open with costs, slippage, and volume limits."""

    def __init__(self, portfolio: PortfolioConfig, costs: CostConfig, initial_cash: float):
        self.portfolio_config = portfolio
        self.costs = costs
        self.ledger = PortfolioLedger(cash=initial_cash)
        self.fills: list[Fill] = []
        self.rejections: list[Fill] = []

    def _round_lot(self, quantity: float) -> int:
        lot_size = self.portfolio_config.lot_size
        return max(0, int(quantity // lot_size) * lot_size)

    def _fees(self, side: Side, gross_value: float) -> tuple[float, float, float, float]:
        commission = max(self.costs.minimum_commission, gross_value * self.costs.commission_rate)
        stamp_tax = gross_value * self.costs.stamp_tax_rate if side == "sell" else 0.0
        transfer_fee = gross_value * self.costs.transfer_fee_rate
        return commission, stamp_tax, transfer_fee, commission + stamp_tax + transfer_fee

    def _rejection(self, order: Order, timestamp: pd.Timestamp, message: str) -> Fill:
        fill = Fill(
            timestamp=timestamp,
            signal_time=order.signal_time,
            side=order.side,
            quantity=0,
            price=0.0,
            gross_value=0.0,
            commission=0.0,
            stamp_tax=0.0,
            transfer_fee=0.0,
            total_fees=0.0,
            cash_flow=0.0,
            reason=order.reason,
            action=order.action,
            status="rejected",
            message=message,
        )
        self.rejections.append(fill)
        return fill

    def execute(
        self,
        order: Order,
        bar: Any,
        up_limit: Optional[float] = None,
        down_limit: Optional[float] = None,
    ) -> Fill:
        """Attempt to fill an order against a single minute bar."""
        timestamp = pd.Timestamp(bar.datetime)
        raw_open = float(bar.open)
        volume = max(float(bar.volume), 0.0)
        if raw_open <= 0 or volume <= 0:
            return self._rejection(order, timestamp, "suspended_or_empty_bar")
        tolerance = max(raw_open * 1e-6, 1e-6)
        if order.side == "buy" and up_limit and float(bar.low) >= up_limit - tolerance:
            return self._rejection(order, timestamp, "locked_at_upper_limit")
        if order.side == "sell" and down_limit and float(bar.high) <= down_limit + tolerance:
            return self._rejection(order, timestamp, "locked_at_lower_limit")

        direction = 1.0 if order.side == "buy" else -1.0
        price = raw_open * (1.0 + direction * self.costs.slippage_bps / 10_000.0)
        volume_cap = self._round_lot(volume * self.portfolio_config.max_volume_participation)
        quantity = min(self._round_lot(order.quantity), volume_cap)
        if order.side == "sell":
            quantity = min(quantity, self._round_lot(self.ledger.sellable_shares))
        else:
            affordable = self._round_lot(self.ledger.cash / max(price * (1.0 + self.costs.commission_rate), 1e-9))
            quantity = min(quantity, affordable)
        if quantity <= 0:
            return self._rejection(order, timestamp, "insufficient_cash_inventory_or_volume")

        gross_value = price * quantity
        commission, stamp_tax, transfer_fee, total_fees = self._fees(order.side, gross_value)
        if order.side == "buy":
            required_cash = gross_value + total_fees
            while quantity > 0 and required_cash > self.ledger.cash:
                quantity -= self.portfolio_config.lot_size
                gross_value = price * quantity
                commission, stamp_tax, transfer_fee, total_fees = self._fees(order.side, gross_value)
                required_cash = gross_value + total_fees
            if quantity <= 0:
                return self._rejection(order, timestamp, "insufficient_cash_after_fees")
            cash_flow = -required_cash
            self.ledger.cash += cash_flow
            self.ledger.total_shares += quantity
        else:
            cash_flow = gross_value - total_fees
            self.ledger.cash += cash_flow
            self.ledger.total_shares -= quantity
            self.ledger.sellable_shares -= quantity

        fill = Fill(
            timestamp=timestamp,
            signal_time=order.signal_time,
            side=order.side,
            quantity=quantity,
            price=price,
            gross_value=gross_value,
            commission=commission,
            stamp_tax=stamp_tax,
            transfer_fee=transfer_fee,
            total_fees=total_fees,
            cash_flow=cash_flow,
            reason=order.reason,
            action=order.action,
        )
        self.fills.append(fill)
        return fill

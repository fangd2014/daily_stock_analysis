"""Aggregate multiple independent paper sleeves into one capped portfolio."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .paper import PaperTradingService
from .paper_config import PaperConfig, load_paper_config
from .paper_report import generate_weekly_report


@dataclass(frozen=True)
class PortfolioPaperConfig:
    name: str
    initial_cash: float
    target_exposure: float
    max_positions: int
    account_configs: list[str]
    report_dir: str
    timezone: str = "Asia/Shanghai"


def load_portfolio_config(path: str | Path) -> PortfolioPaperConfig:
    """Load a portfolio definition and verify capital and position limits."""
    values = json.loads(Path(path).read_text(encoding="utf-8"))
    config = PortfolioPaperConfig(**values)
    if not 0 < config.target_exposure < 1:
        raise ValueError("target_exposure must be between 0 and 1")
    if not 1 <= config.max_positions <= 5:
        raise ValueError("max_positions must be between 1 and 5")
    if len(config.account_configs) != config.max_positions:
        raise ValueError("portfolio account count must equal max_positions")

    accounts = [load_paper_config(account_path) for account_path in config.account_configs]
    capital = sum(_account_cash(account) for account in accounts)
    exposure = sum(_account_cash(account) * _account_base_ratio(account) for account in accounts)
    capital_tolerance = max(config.initial_cash * 1e-9, 0.01)
    if abs(capital - config.initial_cash) > capital_tolerance:
        raise ValueError("paper account capital must sum to portfolio initial_cash")
    exposure_tolerance = config.initial_cash * 0.01
    if abs(exposure - config.initial_cash * config.target_exposure) > exposure_tolerance:
        raise ValueError("paper account base positions must remain within one percentage point of target_exposure")
    return config


def _account_cash(config: PaperConfig) -> float:
    if config.initial_cash is None:
        raise ValueError(f"Portfolio account must declare initial_cash: {config.symbol}")
    return float(config.initial_cash)


def _account_base_ratio(config: PaperConfig) -> float:
    if config.base_ratio is None:
        raise ValueError(f"Portfolio account must declare base_ratio: {config.symbol}")
    return float(config.base_ratio)


class PortfolioPaperService:
    """Run and summarize a fixed set of paper-trading sleeves."""

    def __init__(self, config: PortfolioPaperConfig):
        self.config = config
        self.account_configs = [load_paper_config(path) for path in config.account_configs]
        self.services = [PaperTradingService(account) for account in self.account_configs]

    def initialize(self) -> dict[str, Any]:
        results = [service.initialize() for service in self.services]
        return {"status": "ok", "accounts": results, "portfolio": self.status()["portfolio"]}

    def tick(self, now: datetime | None = None) -> dict[str, Any]:
        results = []
        for account, service in zip(self.account_configs, self.services):
            try:
                result = service.tick(now)
            except Exception as exc:
                result = {"status": "error", "reason": str(exc)}
            results.append({"symbol": account.symbol, "name": account.name, **result})
        status = "error" if all(item.get("status") == "error" for item in results) else "ok"
        return {"status": status, "accounts": results, "portfolio": self.status()["portfolio"]}

    def status(self) -> dict[str, Any]:
        accounts = []
        for config, service in zip(self.account_configs, self.services):
            state = service.status()
            equity = float(state.get("last_equity", _account_cash(config)))
            price = float(state.get("last_price", 0.0))
            shares = int(state.get("total_shares", 0))
            accounts.append(
                {
                    "symbol": config.symbol,
                    "name": config.name,
                    "initial_cash": _account_cash(config),
                    "equity": equity,
                    "cash": float(state.get("cash", _account_cash(config))),
                    "shares": shares,
                    "price": price,
                    "market_value": shares * price,
                    "last_signal": state.get("last_signal"),
                    "active_pair": state.get("active_pair"),
                }
            )
        total_equity = sum(item["equity"] for item in accounts)
        market_value = sum(item["market_value"] for item in accounts)
        portfolio = {
            "name": self.config.name,
            "initial_cash": self.config.initial_cash,
            "equity": total_equity,
            "return": total_equity / self.config.initial_cash - 1.0,
            "cash": sum(item["cash"] for item in accounts),
            "market_value": market_value,
            "exposure": market_value / total_equity if total_equity > 0 else 0.0,
            "target_exposure": self.config.target_exposure,
            "position_count": len(accounts),
            "max_positions": self.config.max_positions,
        }
        return {"status": "ok", "portfolio": portfolio, "accounts": accounts}

    def report(self, now: datetime) -> dict[str, Any]:
        account_reports = [str(generate_weekly_report(config, now)) for config in self.account_configs]
        status = self.status()
        report_dir = Path(self.config.report_dir)
        report_dir.mkdir(parents=True, exist_ok=True)
        portfolio = status["portfolio"]
        lines = [
            f"# {self.config.name}组合模拟盘",
            "",
            f"- 初始资金：¥{self.config.initial_cash:,.2f}",
            f"- 当前权益：¥{portfolio['equity']:,.2f}",
            f"- 累计收益：{portfolio['return']:.2%}",
            f"- 当前仓位：{portfolio['exposure']:.2%}",
            f"- 目标仓位：{self.config.target_exposure:.2%}",
            f"- 持仓上限：{self.config.max_positions} 只",
            "",
            "| 股票 | 初始资金 | 当前权益 | 市值 |",
            "| --- | ---: | ---: | ---: |",
        ]
        for account in status["accounts"]:
            lines.append(
                f"| {account['name']}（{account['symbol']}） | ¥{account['initial_cash']:,.2f} | "
                f"¥{account['equity']:,.2f} | ¥{account['market_value']:,.2f} |"
            )
        lines.extend(["", "该组合仅用于模拟研究，不连接券商，不构成投资建议。"])
        report_path = report_dir / f"portfolio_{now:%Y-%m-%d}.md"
        content = "\n".join(lines) + "\n"
        report_path.write_text(content, encoding="utf-8")
        (report_dir / "latest.md").write_text(content, encoding="utf-8")
        return {"status": "ok", "report": str(report_path), "account_reports": account_reports}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Technology double-peak portfolio paper trading")
    parser.add_argument("--config", required=True)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init")
    subparsers.add_parser("tick")
    subparsers.add_parser("status")
    report = subparsers.add_parser("report")
    report.add_argument("--at")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_portfolio_config(args.config)
    service = PortfolioPaperService(config)
    if args.command == "init":
        result = service.initialize()
    elif args.command == "tick":
        result = service.tick()
    elif args.command == "status":
        result = service.status()
    else:
        now = datetime.fromisoformat(args.at) if args.at else datetime.now(ZoneInfo(config.timezone))
        result = service.report(now)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())

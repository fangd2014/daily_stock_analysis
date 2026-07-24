"""Tests for the post-close paper-trading review."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from src.quant.daily_review import _optimization_advice, generate_daily_review
from src.quant.deepseek_review import build_weekly_context, run_deepseek_review


def _write_review_config(tmp_path: Path) -> Path:
    accounts = []
    allocations = [("AAA", 300_000, 0.4), ("BBB", 300_000, 0.3), ("CCC", 400_000, 0.225)]
    for symbol, cash, base_ratio in allocations:
        state_dir = tmp_path / "state" / symbol
        state_dir.mkdir(parents=True)
        account_path = tmp_path / f"{symbol}.json"
        account_path.write_text(
            json.dumps(
                {
                    "symbol": symbol,
                    "name": symbol,
                    "start_date": "2026-07-23",
                    "quant_config": "unused.json",
                    "initial_cash": cash,
                    "base_ratio": base_ratio,
                    "state_dir": str(state_dir),
                    "report_dir": str(tmp_path / "account_reports" / symbol),
                }
            ),
            encoding="utf-8",
        )
        accounts.append(str(account_path))
    config_path = tmp_path / "portfolio.json"
    config_path.write_text(
        json.dumps(
            {
                "name": "Test portfolio",
                "initial_cash": 1_000_000,
                "target_exposure": 0.3,
                "max_positions": 3,
                "account_configs": accounts,
                "report_dir": str(tmp_path / "portfolio_reports"),
                "timezone": "Asia/Shanghai",
            }
        ),
        encoding="utf-8",
    )
    return config_path


def test_daily_review_separates_rule_errors_from_strategy_losses(tmp_path):
    config_path = _write_review_config(tmp_path)
    state_dir = tmp_path / "state" / "AAA"
    pd.DataFrame(
        [
            {
                "timestamp": "2026-07-23T10:05:00+08:00",
                "signal_time": "2026-07-23T10:00:00+08:00",
                "action": "entry_low",
                "reason": "chip_lower_zone",
                "status": "filled",
                "total_fees": 8.0,
            },
            {
                "timestamp": "2026-07-23T10:14:00+08:00",
                "signal_time": "2026-07-23T10:10:00+08:00",
                "action": "entry_high",
                "reason": "unexpected_reason",
                "status": "rejected",
                "total_fees": 0.0,
            },
        ]
    ).to_csv(state_dir / "trades.csv", index=False)
    pd.DataFrame(
        [
            {
                "direction": "low_buy",
                "entry_time": "2026-07-23T10:05:00+08:00",
                "exit_time": "2026-07-23T10:20:00+08:00",
                "entry_price": 100.0,
                "quantity": 100,
                "pnl": -500.0,
                "exit_reason": "chip_peak_breakout",
            },
            {
                "direction": "high_sell",
                "entry_time": "2026-07-24T10:05:00+08:00",
                "exit_time": "2026-07-24T10:20:00+08:00",
                "entry_price": 100.0,
                "quantity": 100,
                "pnl": 100.0,
                "exit_reason": "mean_reversion",
            },
        ]
    ).to_csv(state_dir / "pairs.csv", index=False)

    report_path = generate_daily_review(config_path, datetime.fromisoformat("2026-07-23T15:25:00+08:00"))
    summary = json.loads(report_path.with_suffix(".json").read_text(encoding="utf-8"))
    content = report_path.read_text(encoding="utf-8")

    assert summary["historical_pair_count"] == 1
    assert {item["category"] for item in summary["mistakes"]} == {
        "execution_rejection",
        "invalid_entry_reason",
        "same_bar_execution",
    }
    assert summary["strategy_losses"][0]["classification"] == "strategy_loss_within_rules"
    assert "不记作操作错误" in content
    assert summary["parameters_changed"] is False


def test_optimization_requires_30_pairs_and_only_enqueues_candidates():
    insufficient = pd.DataFrame(
        [{"pnl": -100.0, "exit_reason": "chip_peak_breakout"} for _ in range(29)]
    )
    eligible = pd.DataFrame(
        [{"pnl": -100.0, "exit_reason": "chip_peak_breakout"} for _ in range(20)]
        + [{"pnl": 50.0, "exit_reason": "mean_reversion"} for _ in range(10)]
    )

    assert "样本不足" in _optimization_advice(insufficient)[0]
    assert _optimization_advice(eligible) == [
        "候选优化：将高抛位置由 72% 提高到 78%，并执行滚动验证。",
        "候选优化：将峰间谷值上限由 70% 收紧至 60%，过滤不稳定双峰。",
    ]


class RecordingReviewGenerator:
    model = "test-review-model"

    def __init__(self):
        self.calls = []

    def analyze(self, review_type, context):
        self.calls.append((review_type, context))
        return f"{review_type} grounded review"


class RecordingReviewNotifier:
    def __init__(self):
        self.messages = []

    def send(self, title, content):
        self.messages.append((title, content))
        return True


def test_deepseek_daily_and_weekly_reviews_are_pushed_idempotently(tmp_path):
    config_path = _write_review_config(tmp_path)
    generator = RecordingReviewGenerator()
    notifier = RecordingReviewNotifier()
    friday = datetime.fromisoformat("2026-07-24T21:00:00+08:00")

    daily = run_deepseek_review(config_path, "daily", generator, notifier, friday)
    weekly_context = build_weekly_context(config_path, friday)
    weekly = run_deepseek_review(config_path, "weekly", generator, notifier, friday)
    repeated_daily = run_deepseek_review(config_path, "daily", generator, notifier, friday)
    repeated_weekly = run_deepseek_review(config_path, "weekly", generator, notifier, friday)

    assert daily["status"] == "ok"
    assert weekly["status"] == "ok"
    assert repeated_daily["reason"] == "review_already_pushed"
    assert repeated_weekly["reason"] == "review_already_pushed"
    assert weekly_context["week_start"] == "2026-07-20"
    assert weekly_context["portfolio"]["weekly_return"] == 0.0
    assert [call[0] for call in generator.calls] == ["daily", "weekly"]
    assert len(notifier.messages) == 2
    report_dir = tmp_path / "portfolio_reports" / "deepseek_reviews"
    assert (report_dir / "daily_2026-07-24.md").exists()
    assert (report_dir / "weekly_2026-07-24.md").exists()

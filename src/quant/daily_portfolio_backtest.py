"""Three-month daily approximation backtest for a selected technology portfolio."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from src.config import setup_env
from src.tushare_client import create_tushare_pro_api

from .backtest import BacktestEngine, BacktestResult
from .config import load_quant_config
from .data import QuantDataBundle, normalize_adjustments, normalize_dividends, normalize_limits
from .metrics import calculate_metrics


def _daily_bundle(api: Any, symbol: str, warmup_start: str, end_date: str) -> QuantDataBundle:
    start_key = warmup_start.replace("-", "")
    end_key = end_date.replace("-", "")
    daily = api.daily(ts_code=symbol, start_date=start_key, end_date=end_key)
    if daily is None or daily.empty:
        raise ValueError(f"Daily history is unavailable for {symbol}")
    daily = daily.sort_values("trade_date").reset_index(drop=True)
    bars = pd.DataFrame(
        {
            "datetime": pd.to_datetime(daily["trade_date"], format="%Y%m%d") + pd.Timedelta(hours=10),
            "symbol": symbol,
            "open": daily["open"],
            "high": daily["high"],
            "low": daily["low"],
            "close": daily["close"],
            "volume": pd.to_numeric(daily["vol"], errors="coerce").fillna(0.0) * 100,
            "amount": pd.to_numeric(daily["amount"], errors="coerce").fillna(0.0) * 1_000,
        }
    )
    adjustments = api.adj_factor(ts_code=symbol, start_date=start_key, end_date=end_key)
    limits = api.stk_limit(ts_code=symbol, start_date=start_key, end_date=end_key)
    try:
        dividends = api.dividend(ts_code=symbol)
    except Exception:
        dividends = pd.DataFrame()
    return QuantDataBundle(
        bars=bars,
        limits=normalize_limits(limits),
        adjustments=normalize_adjustments(adjustments),
        dividends=normalize_dividends(dividends),
    )


def _combine_equity(results: dict[str, BacktestResult]) -> pd.DataFrame:
    series = []
    for symbol, result in results.items():
        values = result.equity.set_index("datetime")["equity"].rename(symbol)
        series.append(values)
    combined = pd.concat(series, axis=1).sort_index().ffill().dropna()
    return pd.DataFrame({"datetime": combined.index, "equity": combined.sum(axis=1).to_numpy()})


def _tag_frames(results: dict[str, BacktestResult], attribute: str) -> pd.DataFrame:
    frames = []
    for symbol, result in results.items():
        frame = getattr(result, attribute)
        if frame.empty:
            continue
        tagged = frame.copy()
        tagged.insert(0, "symbol", symbol)
        frames.append(tagged)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _daily_reviews(
    equity: pd.DataFrame,
    trades: pd.DataFrame,
    pairs: pd.DataFrame,
    rejections: pd.DataFrame,
    output_dir: Path,
) -> pd.DataFrame:
    review_dir = output_dir / "daily_reviews"
    review_dir.mkdir(parents=True, exist_ok=True)
    trade_frame = trades.copy()
    pair_frame = pairs.copy()
    rejection_frame = rejections.copy()
    for frame, column in ((trade_frame, "timestamp"), (pair_frame, "exit_time"), (rejection_frame, "timestamp")):
        if not frame.empty:
            frame["review_date"] = pd.to_datetime(frame[column]).dt.normalize()

    records = []
    dates = pd.to_datetime(equity["datetime"]).dt.normalize().drop_duplicates()
    for trade_date in dates:
        day_trades = trade_frame[trade_frame["review_date"] == trade_date] if not trade_frame.empty else trade_frame
        day_pairs = pair_frame[pair_frame["review_date"] == trade_date] if not pair_frame.empty else pair_frame
        day_rejections = (
            rejection_frame[rejection_frame["review_date"] == trade_date]
            if not rejection_frame.empty
            else rejection_frame
        )
        pnl = float(day_pairs["pnl"].sum()) if not day_pairs.empty else 0.0
        fees = float(day_trades["total_fees"].sum()) if not day_trades.empty else 0.0
        loss_pairs = day_pairs[day_pairs["pnl"] < 0] if not day_pairs.empty else day_pairs
        stop_exits = (
            day_pairs[day_pairs["exit_reason"].isin(["stop_loss", "chip_peak_breakout", "time_stop"])]
            if not day_pairs.empty
            else day_pairs
        )
        violations = len(day_rejections)
        if violations:
            conclusion = "Execution error: inspect rejected orders before the next session."
        elif not stop_exits.empty:
            conclusion = "Strategy loss: review entry location and peak stability; do not change parameters yet."
        elif not loss_pairs.empty:
            conclusion = "Normal losing trade within rules; retain it in the optimization sample."
        elif not day_pairs.empty:
            conclusion = "Rules followed and paired trade closed profitably."
        else:
            conclusion = "No completed pair; no operation error found."
        record = {
            "trade_date": trade_date.strftime("%Y-%m-%d"),
            "trade_count": len(day_trades),
            "pair_count": len(day_pairs),
            "pair_pnl": pnl,
            "fees": fees,
            "loss_pair_count": len(loss_pairs),
            "stop_or_breakout_count": len(stop_exits),
            "rule_violation_count": violations,
            "conclusion": conclusion,
        }
        records.append(record)
        lines = [
            f"# {record['trade_date']} 策略复盘",
            "",
            f"- 成交：{record['trade_count']} 笔",
            f"- 完成配对：{record['pair_count']} 组",
            f"- 配对盈亏：¥{pnl:,.2f}",
            f"- 费用：¥{fees:,.2f}",
            f"- 规则违规：{violations} 项",
            f"- 结论：{conclusion}",
            "",
            "参数优化必须累计足够样本并通过滚动验证，不根据单日盈亏直接修改。",
        ]
        (review_dir / f"{record['trade_date']}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return pd.DataFrame(records)


def _effectiveness(metrics: dict[str, Any], incremental_value: float) -> dict[str, bool]:
    return {
        "positive_incremental_value": incremental_value > 0,
        "profit_factor_above_one": metrics["profit_factor"] > 1,
        "max_drawdown_within_10pct": metrics["max_drawdown"] <= 0.10,
        "at_least_10_pairs": metrics["pair_count"] >= 10,
        "no_rule_rejections": metrics["rejection_count"] == 0,
        "no_unclosed_pairs": not metrics["unclosed_pair"],
    }


def run_backtest(
    selection_path: str | Path,
    template_config_path: str | Path,
    start_date: str,
    end_date: str,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Run a causal daily approximation for the three names selected before the test period."""
    selection = json.loads(Path(selection_path).read_text(encoding="utf-8"))
    selected = selection["selected"]
    if len(selected) != 3:
        raise ValueError("Backtest selection must contain exactly three stocks")
    if any(not item.get("buy_ready", False) for item in selected):
        raise ValueError("Every initial constituent must satisfy the selection-day buy condition")
    setup_env()
    token = os.getenv("TUSHARE_TOKEN", "").strip()
    if not token or token.startswith("your_"):
        raise ValueError("TUSHARE_TOKEN is required for the daily portfolio backtest")
    api = create_tushare_pro_api(token)
    template = load_quant_config(template_config_path)
    warmup_start = (pd.Timestamp(start_date) - pd.Timedelta(days=150)).strftime("%Y-%m-%d")
    capital = [333_333.0, 333_333.0, 333_334.0]
    strategy_results: dict[str, BacktestResult] = {}
    benchmark_results: dict[str, BacktestResult] = {}

    for index, item in enumerate(selected):
        symbol = item["symbol"]
        bundle = _daily_bundle(api, symbol, warmup_start, end_date)
        portfolio = replace(template.portfolio, initial_cash=capital[index], base_ratio=0.30)
        strategy = replace(
            template.strategy,
            position_fraction=0.34,
            max_holding_bars=10,
            no_entry_before="00:00",
            no_entry_after="23:59",
            force_exit_at="23:59",
            daily_trend_limit=0.15,
            daily_volatility_limit=0.10,
        )
        config = replace(
            template,
            symbol=symbol,
            name=item["name"],
            start_date=warmup_start,
            end_date=end_date,
            portfolio=portfolio,
            strategy=strategy,
        )
        strategy_results[symbol] = BacktestEngine(config).run(bundle, evaluation_start=start_date)
        benchmark_results[symbol] = BacktestEngine(config, strategy_enabled=False).run(
            bundle,
            evaluation_start=start_date,
        )

    equity = _combine_equity(strategy_results)
    benchmark_equity = _combine_equity(benchmark_results)
    trades = _tag_frames(strategy_results, "trades")
    pairs = _tag_frames(strategy_results, "pairs")
    rejections = _tag_frames(strategy_results, "rejections")
    metrics = calculate_metrics(equity, trades, pairs, initial_equity=1_000_000.0)
    metrics["rejection_count"] = len(rejections)
    metrics["unclosed_pair"] = any(result.open_pair for result in strategy_results.values())
    metrics["total_fees"] = float(trades["total_fees"].sum()) if not trades.empty else 0.0
    metrics["t0_pair_pnl"] = float(pairs["pnl"].sum()) if not pairs.empty else 0.0
    benchmark_metrics = calculate_metrics(benchmark_equity, initial_equity=1_000_000.0)
    incremental_value = metrics["end_equity"] - benchmark_metrics["end_equity"]
    checks = _effectiveness(metrics, incremental_value)

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    equity.to_csv(target_dir / "portfolio_equity.csv", index=False)
    benchmark_equity.to_csv(target_dir / "benchmark_equity.csv", index=False)
    trades.to_csv(target_dir / "trades.csv", index=False)
    pairs.to_csv(target_dir / "pairs.csv", index=False)
    rejections.to_csv(target_dir / "rejections.csv", index=False)
    reviews = _daily_reviews(equity, trades, pairs, rejections, target_dir)
    reviews.to_csv(target_dir / "daily_reviews.csv", index=False)

    summary = {
        "period": {"start": start_date, "end": end_date},
        "selection_as_of": selection["as_of"],
        "selected": selected,
        "method": "daily_close_signal_next_open_daily_approximation",
        "portfolio": metrics,
        "static_base_benchmark": benchmark_metrics,
        "incremental_end_value": incremental_value,
        "effectiveness_checks": checks,
        "effective": all(checks.values()),
        "individual": {symbol: result.metrics for symbol, result in strategy_results.items()},
    }
    (target_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    if summary["effective"]:
        conclusion = "初步有效（仍需分钟级与滚动验证）"
    else:
        conclusion = "证据不足/未通过"
    lines = [
        "# 科技龙头筹码双峰策略三个月回测报告",
        "",
        f"回测区间：{start_date} 至 {end_date}",
        f"选股时点：{selection['as_of']}（仅使用该日及以前的数据）",
        f"初始持仓：{'、'.join(item['name'] for item in selected)}",
        "",
        "## 组合结果",
        "",
        f"- 策略收益：{metrics['total_return']:.2%}",
        f"- 静态底仓收益：{benchmark_metrics['total_return']:.2%}",
        f"- 策略增量收益：¥{incremental_value:,.2f}（占初始资金 {incremental_value / 1_000_000:.2%}）",
        f"- 最大回撤：{metrics['max_drawdown']:.2%}",
        f"- 完成配对：{metrics['pair_count']} 组",
        f"- 胜率：{metrics['win_rate']:.2%}",
        f"- 盈亏比：{metrics['profit_factor']:.2f}",
        f"- 费用：¥{metrics['total_fees']:,.2f}",
        f"- 结论：{conclusion}",
        "",
        "## 有效性检查",
        "",
    ]
    for name, passed in checks.items():
        lines.append(f"- {'PASS' if passed else 'FAIL'}：{name}")
    lines.extend(
        [
            "",
            "## 方法与限制",
            "",
            "- 双峰和买入资格只使用选股日及以前数据，避免期末选股回看偏差。",
            "- 由于可用5分钟数据仅覆盖约31个交易日，本报告使用完整日线作近似验证。",
            "- 信号在日线收盘确认，下一交易日开盘成交，最长持有10个交易日。",
            "- 日线结果验证双峰均值回归逻辑，不等同于5分钟模拟盘的精确成交表现。",
            "- 参数不会根据单日亏损自动修改；建议累计至少30组配对后再做滚动优化。",
        ]
    )
    (target_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily approximation portfolio backtest")
    parser.add_argument("--selection", required=True)
    parser.add_argument("--template-config", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    summary = run_backtest(args.selection, args.template_config, args.start, args.end, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

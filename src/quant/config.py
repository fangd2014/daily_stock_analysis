"""Configuration models for the quantitative backtesting module."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd


@dataclass(frozen=True)
class DataConfig:
    source: str = "tushare"
    fallback_source: str = "tushare"
    frequency: str = "5min"
    cache_dir: str = "data/quant_cache"
    request_pause_seconds: float = 61.0
    minute_chunk_months: int = 6
    max_chunks_per_run: int = 0
    local_path: str = ""
    tdx_endpoints: List[str] = field(default_factory=list)
    tdx_connect_timeout_seconds: float = 2.0
    tdx_health_ttl_seconds: int = 3600
    tdx_probe_workers: int = 12
    tdx_page_size: int = 800
    tdx_max_pages: int = 500
    tdx_min_complete_day_ratio: float = 0.95
    tdx_min_bars_per_day_ratio: float = 0.80
    tdx_close_tolerance_bps: float = 20.0
    tdx_require_cross_validation: bool = True
    tdx_history_start_date: str = ""


@dataclass(frozen=True)
class PortfolioConfig:
    initial_cash: float = 1_000_000.0
    base_ratio: float = 0.60
    lot_size: int = 100
    max_volume_participation: float = 0.05


@dataclass(frozen=True)
class CostConfig:
    commission_rate: float = 0.00025
    minimum_commission: float = 5.0
    stamp_tax_rate: float = 0.0005
    transfer_fee_rate: float = 0.00001
    slippage_bps: float = 2.0


@dataclass(frozen=True)
class StrategyConfig:
    strategy_type: str = "vwap_t0"
    zscore_window: int = 18
    entry_z: float = 1.50
    exit_z: float = 0.25
    position_fraction: float = 0.20
    stop_loss_pct: float = 0.006
    max_holding_bars: int = 12
    max_pairs_per_day: int = 2
    min_edge_bps: float = 20.0
    no_entry_before: str = "09:45"
    no_entry_after: str = "14:30"
    force_exit_at: str = "14:50"
    daily_loss_limit: float = 0.006
    daily_trend_limit: float = 0.08
    daily_volatility_limit: float = 0.06
    intraday_vwap_drift_limit: float = 0.015
    account_drawdown_limit: float = 0.25
    chip_lookback_days: int = 60
    chip_min_history_days: int = 20
    chip_bins: int = 48
    chip_min_peak_separation_pct: float = 0.08
    chip_min_peak_height_ratio: float = 0.45
    chip_max_valley_ratio: float = 0.70
    chip_low_entry_position: float = 0.28
    chip_high_entry_position: float = 0.72
    chip_exit_position: float = 0.50
    chip_breakout_buffer_pct: float = 0.01


@dataclass(frozen=True)
class OptimizationConfig:
    train_months: int = 24
    validation_months: int = 6
    step_months: int = 6
    holdout_start: str = "2025-07-01"
    holdout_end: str = "2026-06-30"
    min_trades_per_year: int = 24
    zscore_windows: List[int] = field(default_factory=lambda: [12, 18, 24])
    entry_z_values: List[float] = field(default_factory=lambda: [1.25, 1.50, 1.75, 2.00])
    exit_z_values: List[float] = field(default_factory=lambda: [0.0, 0.25, 0.50])
    position_fractions: List[float] = field(default_factory=lambda: [0.10, 0.20, 0.30])
    stop_loss_values: List[float] = field(default_factory=lambda: [0.004, 0.006, 0.008])
    chip_lookback_days_values: List[int] = field(default_factory=lambda: [40, 60, 80])
    chip_low_entry_position_values: List[float] = field(default_factory=lambda: [0.22, 0.28, 0.34])
    chip_high_entry_position_values: List[float] = field(default_factory=lambda: [0.66, 0.72, 0.78])
    chip_exit_position_values: List[float] = field(default_factory=lambda: [0.45, 0.50, 0.55])


@dataclass(frozen=True)
class AcceptanceConfig:
    annual_return_min: float = 0.30
    max_drawdown_max: float = 0.25
    calmar_min: float = 2.0
    mean_monthly_return_min: float = 0.025
    median_monthly_return_min: float = -1.0
    min_out_of_sample_months: int = 0


@dataclass(frozen=True)
class QuantConfig:
    symbol: str
    name: str
    start_date: str
    end_date: str
    output_dir: str
    data: DataConfig = field(default_factory=DataConfig)
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    optimization: OptimizationConfig = field(default_factory=OptimizationConfig)
    acceptance: AcceptanceConfig = field(default_factory=AcceptanceConfig)

    def with_strategy(self, **values: Any) -> "QuantConfig":
        """Return a new configuration with selected strategy values replaced."""
        return replace(self, strategy=replace(self.strategy, **values))

    def to_dict(self) -> Dict[str, Any]:
        """Convert the complete configuration to JSON-compatible values."""
        return asdict(self)


def _build_section(model: Any, values: Dict[str, Any] | None) -> Any:
    return model(**(values or {}))


def load_quant_config(path: str | Path) -> QuantConfig:
    """Load and validate a quantitative backtest configuration file."""
    config_path = Path(path)
    values = json.loads(config_path.read_text(encoding="utf-8"))
    config = QuantConfig(
        symbol=values["symbol"],
        name=values.get("name", values["symbol"]),
        start_date=values["start_date"],
        end_date=values["end_date"],
        output_dir=values.get("output_dir", f"reports/quant/{values['symbol'].replace('.', '_')}"),
        data=_build_section(DataConfig, values.get("data")),
        portfolio=_build_section(PortfolioConfig, values.get("portfolio")),
        costs=_build_section(CostConfig, values.get("costs")),
        strategy=_build_section(StrategyConfig, values.get("strategy")),
        optimization=_build_section(OptimizationConfig, values.get("optimization")),
        acceptance=_build_section(AcceptanceConfig, values.get("acceptance")),
    )
    _validate_config(config)
    return config


def _validate_config(config: QuantConfig) -> None:
    if config.data.source not in {"tushare", "local", "pytdx", "hybrid"}:
        raise ValueError(f"Unsupported data source: {config.data.source}")
    if config.data.source == "local" and not config.data.local_path:
        raise ValueError("data.local_path is required when data.source is local")
    if config.data.fallback_source not in {"none", "tushare", "local"}:
        raise ValueError(f"Unsupported fallback data source: {config.data.fallback_source}")
    if config.data.source == "pytdx" and config.data.fallback_source == "local" and not config.data.local_path:
        raise ValueError("data.local_path is required when data.fallback_source is local")
    if config.data.frequency not in {"1min", "5min", "15min", "30min", "60min"}:
        raise ValueError(f"Unsupported frequency: {config.data.frequency}")
    if config.data.tdx_connect_timeout_seconds <= 0:
        raise ValueError("data.tdx_connect_timeout_seconds must be positive")
    if config.data.tdx_health_ttl_seconds < 0:
        raise ValueError("data.tdx_health_ttl_seconds must not be negative")
    if config.data.tdx_probe_workers <= 0:
        raise ValueError("data.tdx_probe_workers must be positive")
    if not 1 <= config.data.tdx_page_size <= 800:
        raise ValueError("data.tdx_page_size must be between 1 and 800")
    if config.data.tdx_max_pages <= 0:
        raise ValueError("data.tdx_max_pages must be positive")
    if not 0 < config.data.tdx_min_complete_day_ratio <= 1:
        raise ValueError("data.tdx_min_complete_day_ratio must be in (0, 1]")
    if not 0 < config.data.tdx_min_bars_per_day_ratio <= 1:
        raise ValueError("data.tdx_min_bars_per_day_ratio must be in (0, 1]")
    if config.data.tdx_close_tolerance_bps <= 0:
        raise ValueError("data.tdx_close_tolerance_bps must be positive")
    if config.data.source == "hybrid":
        if not config.data.tdx_history_start_date:
            raise ValueError("data.tdx_history_start_date is required when data.source is hybrid")
        cutoff = pd.Timestamp(config.data.tdx_history_start_date)
        if not pd.Timestamp(config.start_date) < cutoff <= pd.Timestamp(config.end_date):
            raise ValueError("data.tdx_history_start_date must be inside the configured date range")
    if config.strategy.strategy_type not in {"vwap_t0", "chip_double_peak"}:
        raise ValueError(f"Unsupported strategy type: {config.strategy.strategy_type}")
    if not 0 < config.portfolio.base_ratio < 1:
        raise ValueError("portfolio.base_ratio must be between 0 and 1")
    if config.portfolio.lot_size <= 0:
        raise ValueError("portfolio.lot_size must be positive")
    if not 0 < config.strategy.position_fraction <= 1:
        raise ValueError("strategy.position_fraction must be in (0, 1]")
    if config.strategy.exit_z >= config.strategy.entry_z:
        raise ValueError("strategy.exit_z must be lower than strategy.entry_z")
    if not 0 < config.strategy.account_drawdown_limit < 1:
        raise ValueError("strategy.account_drawdown_limit must be between 0 and 1")
    if config.strategy.chip_lookback_days < config.strategy.chip_min_history_days:
        raise ValueError("strategy.chip_lookback_days must be at least chip_min_history_days")
    if config.strategy.chip_min_history_days < 2:
        raise ValueError("strategy.chip_min_history_days must be at least 2")
    if config.strategy.chip_bins < 8:
        raise ValueError("strategy.chip_bins must be at least 8")
    if not 0 < config.strategy.chip_min_peak_separation_pct < 1:
        raise ValueError("strategy.chip_min_peak_separation_pct must be between 0 and 1")
    if not 0 < config.strategy.chip_min_peak_height_ratio <= 1:
        raise ValueError("strategy.chip_min_peak_height_ratio must be in (0, 1]")
    if not 0 < config.strategy.chip_max_valley_ratio < 1:
        raise ValueError("strategy.chip_max_valley_ratio must be between 0 and 1")
    if not (
        0 < config.strategy.chip_low_entry_position
        < config.strategy.chip_exit_position
        < config.strategy.chip_high_entry_position
        < 1
    ):
        raise ValueError("chip positions must satisfy 0 < low < exit < high < 1")
    if config.strategy.chip_breakout_buffer_pct <= 0:
        raise ValueError("strategy.chip_breakout_buffer_pct must be positive")
    if config.acceptance.min_out_of_sample_months < 0:
        raise ValueError("acceptance.min_out_of_sample_months must not be negative")
    if config.acceptance.median_monthly_return_min < -1:
        raise ValueError("acceptance.median_monthly_return_min must be at least -1")
    holdout_start = pd.Timestamp(config.optimization.holdout_start)
    holdout_end = pd.Timestamp(config.optimization.holdout_end)
    if holdout_end < holdout_start:
        raise ValueError("optimization.holdout_end must not precede holdout_start")
    configured_oos_months = len(pd.period_range(holdout_start, holdout_end, freq="M"))
    if configured_oos_months < config.acceptance.min_out_of_sample_months:
        raise ValueError(
            "Configured holdout has fewer months than acceptance.min_out_of_sample_months"
        )

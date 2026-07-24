"""Configuration for persistent paper trading."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class PaperConfig:
    symbol: str
    name: str
    start_date: str
    quant_config: str
    state_dir: str
    report_dir: str
    timezone: str = "Asia/Shanghai"
    quote_source: str = "tencent"
    quant_symbol: str = ""
    initial_cash: Optional[float] = None
    base_ratio: Optional[float] = None
    position_fraction: Optional[float] = None
    selection_lower_peak: Optional[float] = None
    selection_upper_peak: Optional[float] = None
    selection_buy_position_max: float = 0.45
    max_initial_entry_gap_pct: float = 0.03


def load_paper_config(path: str | Path) -> PaperConfig:
    """Load a paper trading JSON configuration."""
    values = json.loads(Path(path).read_text(encoding="utf-8"))
    config = PaperConfig(**values)
    if config.quote_source != "tencent":
        raise ValueError("Only the lightweight Tencent quote source is supported for paper trading")
    if config.initial_cash is not None and config.initial_cash <= 0:
        raise ValueError("initial_cash must be positive when provided")
    if config.base_ratio is not None and not 0 < config.base_ratio < 1:
        raise ValueError("base_ratio must be between 0 and 1 when provided")
    if config.position_fraction is not None and not 0 < config.position_fraction <= 1:
        raise ValueError("position_fraction must be in (0, 1] when provided")
    peaks = (config.selection_lower_peak, config.selection_upper_peak)
    if any(value is not None for value in peaks):
        if any(value is None for value in peaks):
            raise ValueError("selection_lower_peak and selection_upper_peak must be provided together")
        if not 0 < float(config.selection_lower_peak) < float(config.selection_upper_peak):
            raise ValueError("selection chip peaks must be positive and ordered")
    if not 0 < config.selection_buy_position_max < 0.5:
        raise ValueError("selection_buy_position_max must be between 0 and 0.5")
    if not 0 <= config.max_initial_entry_gap_pct <= 0.2:
        raise ValueError("max_initial_entry_gap_pct must be between 0 and 0.2")
    return config

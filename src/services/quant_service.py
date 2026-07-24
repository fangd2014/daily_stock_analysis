"""Application service for safe, observable quantitative strategy runs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"completed", "failed"}
MAX_LOG_LINES = 1_000
MAX_RUNS = 50


def _now() -> datetime:
    return datetime.now(SHANGHAI)


@dataclass(frozen=True)
class StrategySpec:
    """Registered strategy metadata and its trusted command factory."""

    strategy_id: str
    name: str
    category: str
    summary: str
    schedule: str
    data_source: str
    config_path: str
    report_hint: str
    factors: tuple[str, ...]
    entry_rules: tuple[str, ...]
    exit_rules: tuple[str, ...]
    risk_rules: tuple[str, ...]
    command_factory: Callable[[Path], list[str]]


@dataclass
class QuantLogEntry:
    """One ordered execution log line."""

    sequence: int
    timestamp: str
    level: str
    message: str


@dataclass
class QuantRun:
    """Observable lifecycle state for one strategy execution."""

    run_id: str
    strategy_id: str
    strategy_name: str
    status: str = "queued"
    progress: int = 0
    created_at: str = field(default_factory=lambda: _now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    exit_code: Optional[int] = None
    logs: list[QuantLogEntry] = field(default_factory=list)
    result: Optional[dict[str, Any]] = None

    def to_dict(self, include_logs: bool = True) -> dict[str, Any]:
        payload = asdict(self)
        if not include_logs:
            payload.pop("logs", None)
        return payload


class QuantRunConflictError(RuntimeError):
    """Raised when a strategy already has an active run."""

    def __init__(self, strategy_id: str, run_id: str) -> None:
        self.strategy_id = strategy_id
        self.run_id = run_id
        super().__init__(f"Strategy {strategy_id} is already running ({run_id})")


def _python_module(module: str, *arguments: str) -> list[str]:
    return [sys.executable, "-u", "-m", module, *arguments]


def _latest_chip_date(root: Path) -> str:
    path = root / "reports/quant/daily_chip_screen/latest_selection.json"
    if path.exists():
        try:
            value = str(json.loads(path.read_text(encoding="utf-8")).get("as_of", ""))
            if len(value) == 8 and value.isdigit():
                return f"{value[:4]}-{value[4:6]}-{value[6:]}T20:00:00"
        except (OSError, ValueError, TypeError):
            pass
    return _now().replace(hour=20, minute=0, second=0, microsecond=0).isoformat()


def _build_specs() -> dict[str, StrategySpec]:
    specs = (
        StrategySpec(
            strategy_id="chip-double-peak",
            name="筹码双峰低位选股",
            category="A股收盘选股",
            summary=(
                "在行业上涨且主力净流入的股票中识别明显双峰，"
                "按距离低筹码峰由近到远选出10只。"
            ),
            schedule="交易日 20:00",
            data_source="Tushare日线、资金流与行业分类",
            config_path="configs/quant/daily_chip_screen.json",
            report_hint="reports/quant/daily_chip_screen/latest_selection.json",
            factors=(
                "60日成交量价格分布",
                "双峰间距与谷深",
                "行业当日涨幅",
                "主力净买入",
                "低峰距离",
            ),
            entry_rules=(
                "股价位于两个筹码峰之间，且峰间位置不高于45%",
                "所属行业当日平均涨幅为正，股票当日大单与特大单净流入为正",
                "20日平均成交额不低于1亿元，并排除ST与退市标记股票",
            ),
            exit_rules=(
                "本策略负责生成观察名单，实际模拟持仓按独立组合风控执行",
                "股价离开低位买入区或开盘条件失效时取消计划",
            ),
            risk_rules=(
                "只选择完全通过条件的股票，不足10只时不使用不合格股票补位",
                "信号日成交量不进入筹码分布，防止未来数据泄漏",
            ),
            command_factory=lambda root: _python_module(
                "src.quant.daily_chip_screener",
                "--config",
                str(root / "configs/quant/daily_chip_screen.json"),
                "screen",
            ),
        ),
        StrategySpec(
            strategy_id="hot-sector-commanders",
            name="热门板块中军",
            category="短线主题选股",
            summary=(
                "每日跟踪强势行业，从成交活跃、趋势向上且主力净流入的"
                "板块中军中选出最多10只。"
            ),
            schedule="交易日 20:05",
            data_source="Tushare日线、资金流与行业分类",
            config_path="configs/quant/hot_sector_screen.json",
            report_hint="reports/quant/hot_sector_screen/latest_selection.json",
            factors=(
                "行业涨幅",
                "上涨广度",
                "行业成交活跃度",
                "20日动量",
                "个股成交额与资金流",
            ),
            entry_rules=(
                "只进入涨幅至少0.8%、上涨广度至少60%的前5个热门行业",
                "个股属于板块成交额前40%，放量、主力净流入且距离MA20不超过8%",
                "单行业最多3只，全市场最多10只；高开超过3%不追",
            ),
            exit_rules=(
                "板块热度退潮或个股跌破失效位时退出",
                "最长持有5个交易日，并按报告中的两档止盈执行",
            ),
            risk_rules=(
                "个股当日涨幅超过7%时排除，避免追高",
                "没有合格热点时保持空名单和现金",
            ),
            command_factory=lambda root: _python_module(
                "src.quant.hot_sector_screener",
                "--config",
                str(root / "configs/quant/hot_sector_screen.json"),
                "screen",
            ),
        ),
        StrategySpec(
            strategy_id="four-factor",
            name="四因子时点选股",
            category="机器学习选股",
            summary=(
                "组合指数动量、舆情语义、区间学习与特征归因，"
                "次日再由真实竞价异动确认。"
            ),
            schedule="前夜20:00观察，次日09:25确认",
            data_source="Tushare缓存、新闻搜索、DeepSeek与TickDB",
            config_path="configs/quant/four_factor_strategy.json",
            report_hint="reports/quant/four_factor_live/*/result.json",
            factors=("指数动量25%", "舆情语义20%", "区间学习与归因35%", "竞价异动联动20%"),
            entry_rules=(
                "前夜三因子先生成最多10只观察名单",
                (
                    "次日09:25跳空、竞价量比、行业广度和未匹配买卖盘全部通过后"
                    "才允许模拟买入"
                ),
                "最终最多5只，单行业最多2只",
            ),
            exit_rules=("单股达到8%后分批止盈，15%执行第二档止盈", "单股亏损5%触发止损"),
            risk_rules=(
                "目标总仓位60%，单股最高12%",
                "组合回撤达到12%停止交易",
                "缺少真实竞价数据时禁止自动买入",
            ),
            command_factory=lambda root: _python_module(
                "src.quant.four_factor_live",
                "--as-of",
                _latest_chip_date(root),
            ),
        ),
        StrategySpec(
            strategy_id="etf-momentum",
            name="ETF动量轮动",
            category="跨资产轮动",
            summary=(
                "从15只跨资产ETF中按风险调整动量周频轮动，最多持有3只、目标总仓位60%。"
            ),
            schedule="每周日 20:00",
            data_source="Tushare前复权ETF行情与TickDB交易日历",
            config_path="configs/quant/etf_momentum_rotation.json",
            report_hint="reports/quant/etf_momentum/latest.json",
            factors=(
                "20/60/120/250日动量",
                "MA120趋势门槛",
                "60日波动率惩罚",
                "120日回撤惩罚",
                "资产类别分散",
            ),
            entry_rules=(
                "收盘高于MA120且120日收益为正",
                "按风险调整动量排序，最多3只且每类资产最多1只",
                "下一交易日开盘执行，高开超过前收3%放弃追入",
            ),
            exit_rules=(
                "单只固定止损7%",
                "从持有期高点回撤6%触发移动止损",
                "周频信号替换弱势ETF",
            ),
            risk_rules=(
                "目标仓位60%，单只目标20%",
                "组合回撤8%降至30%仓位",
                "组合回撤12%清仓并冷静20个交易日",
            ),
            command_factory=lambda root: _python_module(
                "src.quant.etf_momentum",
                "--config",
                str(root / "configs/quant/etf_momentum_rotation.json"),
                "research",
            ),
        ),
    )
    return {spec.strategy_id: spec for spec in specs}


class QuantStrategyService:
    """Expose registered quant strategies and execute them without shell interpolation."""

    _instance: Optional["QuantStrategyService"] = None
    _instance_lock = threading.Lock()

    def __new__(cls, *args: Any, **kwargs: Any) -> "QuantStrategyService":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, root: Optional[Path] = None, max_workers: int = 2) -> None:
        if getattr(self, "_initialized", False):
            return
        self.root = (root or Path(__file__).resolve().parents[2]).resolve()
        self.specs = _build_specs()
        self._runs: dict[str, QuantRun] = {}
        self._active_by_strategy: dict[str, str] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="quant_web")
        self._run_dir = self.root / "reports/quant/web_runs"
        self._load_persisted_runs()
        self._initialized = True

    def list_strategies(self) -> list[dict[str, Any]]:
        """Return the strategy catalog together with latest persisted outputs."""
        with self._lock:
            latest_runs = {
                strategy_id: max(
                    (run for run in self._runs.values() if run.strategy_id == strategy_id),
                    key=lambda run: run.created_at,
                    default=None,
                )
                for strategy_id in self.specs
            }
        return [self._strategy_payload(spec, latest_runs.get(spec.strategy_id)) for spec in self.specs.values()]

    def get_strategy(self, strategy_id: str) -> Optional[dict[str, Any]]:
        """Return one registered strategy or None when it does not exist."""
        spec = self.specs.get(strategy_id)
        if spec is None:
            return None
        with self._lock:
            latest_run = max(
                (run for run in self._runs.values() if run.strategy_id == strategy_id),
                key=lambda run: run.created_at,
                default=None,
            )
        return self._strategy_payload(spec, latest_run)

    def start_run(self, strategy_id: str) -> QuantRun:
        """Queue one trusted strategy command and return immediately."""
        spec = self.specs.get(strategy_id)
        if spec is None:
            raise KeyError(strategy_id)
        with self._lock:
            active_run_id = self._active_by_strategy.get(strategy_id)
            if active_run_id:
                raise QuantRunConflictError(strategy_id, active_run_id)
            run = QuantRun(
                run_id=uuid.uuid4().hex,
                strategy_id=strategy_id,
                strategy_name=spec.name,
                progress=2,
            )
            self._runs[run.run_id] = run
            self._active_by_strategy[strategy_id] = run.run_id
            self._append_log(run, "INFO", "Strategy run queued")
            self._trim_runs_locked()
            self._persist_run(run)
        self._executor.submit(self._execute_run, run.run_id)
        return self._copy_run(run)

    def get_run(self, run_id: str) -> Optional[QuantRun]:
        """Return a detached copy of one run."""
        with self._lock:
            run = self._runs.get(run_id)
            return self._copy_run(run) if run else None

    def list_runs(self, limit: int = 20) -> list[QuantRun]:
        """Return recent runs in reverse creation order."""
        with self._lock:
            values = sorted(self._runs.values(), key=lambda run: run.created_at, reverse=True)[:limit]
            return [self._copy_run(run) for run in values]

    def _execute_run(self, run_id: str) -> None:
        with self._lock:
            run = self._runs[run_id]
            spec = self.specs[run.strategy_id]
            run.status = "running"
            run.progress = 8
            run.started_at = _now().isoformat()
            command = spec.command_factory(self.root)
            self._append_log(run, "INFO", f"Starting {spec.name}")
            self._append_log(run, "INFO", f"Using configuration {spec.config_path}")
            self._persist_run(run)

        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        try:
            process = subprocess.Popen(
                command,
                cwd=self.root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                message = line.rstrip()
                if not message:
                    continue
                with self._lock:
                    current = self._runs[run_id]
                    self._append_log(current, self._log_level(message), message)
                    current.progress = min(90, max(current.progress, 10 + len(current.logs) // 4))
            exit_code = process.wait()
            with self._lock:
                current = self._runs[run_id]
                current.exit_code = exit_code
                current.completed_at = _now().isoformat()
                if exit_code == 0:
                    current.status = "completed"
                    current.progress = 100
                    current.result = self._load_latest_result(spec)
                    self._append_log(current, "INFO", "Strategy run completed and latest result refreshed")
                else:
                    current.status = "failed"
                    current.error = f"Strategy process exited with code {exit_code}"
                    self._append_log(current, "ERROR", current.error)
                self._active_by_strategy.pop(current.strategy_id, None)
                self._persist_run(current)
        except Exception as exc:
            with self._lock:
                current = self._runs[run_id]
                current.status = "failed"
                current.completed_at = _now().isoformat()
                current.error = str(exc)
                self._append_log(current, "ERROR", f"Strategy run failed: {exc}")
                self._active_by_strategy.pop(current.strategy_id, None)
                self._persist_run(current)

    def _strategy_payload(self, spec: StrategySpec, latest_run: Optional[QuantRun]) -> dict[str, Any]:
        config = self._read_json(self.root / spec.config_path) or {}
        active_run_id = self._active_by_strategy.get(spec.strategy_id)
        return {
            "strategy_id": spec.strategy_id,
            "name": spec.name,
            "category": spec.category,
            "summary": spec.summary,
            "schedule": spec.schedule,
            "data_source": spec.data_source,
            "config_path": spec.config_path,
            "status": "running" if active_run_id else "ready",
            "active_run_id": active_run_id,
            "factors": list(spec.factors),
            "entry_rules": list(spec.entry_rules),
            "exit_rules": list(spec.exit_rules),
            "risk_rules": list(spec.risk_rules),
            "parameters": self._public_parameters(config),
            "latest_result": self._load_latest_result(spec),
            "latest_run": latest_run.to_dict(include_logs=False) if latest_run else None,
        }

    def _load_latest_result(self, spec: StrategySpec) -> Optional[dict[str, Any]]:
        path = self._resolve_latest_report(spec)
        if path is None:
            return None
        payload = self._read_json(path)
        if payload is None:
            return None
        if spec.strategy_id == "etf-momentum":
            result = self._normalize_etf(payload)
        elif spec.strategy_id == "chip-double-peak":
            result = self._normalize_chip(payload)
        elif spec.strategy_id == "hot-sector-commanders":
            result = self._normalize_hot_sector(payload)
        else:
            result = self._normalize_four_factor(payload)
        result["updated_at"] = datetime.fromtimestamp(path.stat().st_mtime, tz=SHANGHAI).isoformat()
        result["report_path"] = str(path.relative_to(self.root))
        return result

    def _resolve_latest_report(self, spec: StrategySpec) -> Optional[Path]:
        if "*" not in spec.report_hint:
            path = self.root / spec.report_hint
            return path if path.exists() else None
        candidates = list(self.root.glob(spec.report_hint))
        return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None

    @staticmethod
    def _normalize_etf(payload: dict[str, Any]) -> dict[str, Any]:
        plan = payload.get("plan", {})
        selections = []
        for item in plan.get("selected", []):
            selections.append(
                {
                    "symbol": item.get("symbol"),
                    "name": item.get("name"),
                    "group": item.get("asset_class"),
                    "price": item.get("close"),
                    "score": item.get("score"),
                    "target_weight": item.get("target_weight"),
                    "buy_reference": item.get("max_open_price"),
                    "stop_reference": item.get("initial_stop"),
                    "reason": "绝对趋势门槛通过，风险调整动量排名靠前",
                }
            )
        guidance = [
            (
                f"计划于 {plan.get('execute_date', '下一交易日')} 开盘执行；"
                "高于报告放弃价时不追入。"
            ),
            "单只目标仓位20%，先检查当前持仓与资产类别上限。",
            "固定止损7%、移动止损6%；组合回撤8%降仓，12%清仓并进入冷静期。",
        ]
        return {
            "as_of": plan.get("signal_date"),
            "execute_date": plan.get("execute_date"),
            "selected_count": len(selections),
            "selections": selections,
            "metrics": payload.get("metrics", {}),
            "guidance": guidance,
            "data_errors": payload.get("data_errors", {}),
        }

    def _normalize_chip(self, payload: dict[str, Any]) -> dict[str, Any]:
        guidance_by_symbol = self._guidance_by_symbol(
            self.root / "reports/quant/daily_chip_screen/latest_guidance.json"
        )
        selections = []
        for item in payload.get("selected", []):
            symbol = str(item.get("symbol", ""))
            selections.append(
                {
                    "symbol": symbol,
                    "name": item.get("name"),
                    "group": item.get("industry"),
                    "price": item.get("close"),
                    "score": item.get("distance_to_lower_pct"),
                    "target_weight": None,
                    "buy_reference": item.get("lower_peak"),
                    "stop_reference": None,
                    "reason": (
                        f"距低筹码峰 {self._percent(item.get('distance_to_lower_pct'))}，"
                        f"行业涨幅 {self._percent_points(item.get('sector_pct_chg'))}，主力净流入"
                    ),
                    "operation_guide": guidance_by_symbol.get(symbol),
                }
            )
        return {
            "as_of": payload.get("as_of"),
            "selected_count": len(selections),
            "eligible_count": payload.get("eligible_count"),
            "selections": selections,
            "metrics": {},
            "guidance": [
                (
                    "名单按距离低筹码峰从近到远排序；次日仍需确认未高开超过3%"
                    "且价格没有离开低位区。"
                ),
                "筛选结果不是自动成交指令；没有明确止损和仓位计划时保持观察。",
            ],
        }

    @staticmethod
    def _normalize_hot_sector(payload: dict[str, Any]) -> dict[str, Any]:
        selections = []
        for item in payload.get("selected", []):
            selections.append(
                {
                    "symbol": item.get("symbol"),
                    "name": item.get("name"),
                    "group": item.get("industry") or item.get("sector"),
                    "price": item.get("close"),
                    "score": item.get("score") or item.get("commander_score"),
                    "target_weight": item.get("target_weight"),
                    "buy_reference": item.get("buy_zone_high") or item.get("max_open_price"),
                    "stop_reference": item.get("stop_loss"),
                    "reason": item.get("reason", "热门行业中的流动性中军，趋势和资金门槛通过"),
                    "operation_guide": item.get("guidance"),
                }
            )
        return {
            "as_of": payload.get("as_of"),
            "selected_count": len(selections),
            "eligible_count": payload.get("eligible_count"),
            "hot_sectors": payload.get("hot_sectors", []),
            "selections": selections,
            "metrics": {},
            "guidance": [
                "开盘高于前收3%不追；板块上涨广度跌破策略门槛时取消计划。",
                "最多持有5个交易日，严格执行个股止损和两档止盈。",
            ],
        }

    @staticmethod
    def _normalize_four_factor(payload: dict[str, Any]) -> dict[str, Any]:
        rows = payload.get("watchlist", payload.get("selected", []))
        selections = []
        for item in rows:
            selections.append(
                {
                    "symbol": item.get("symbol"),
                    "name": item.get("name"),
                    "group": item.get("industry") or item.get("index_symbol"),
                    "price": item.get("tickdb_last_done") or item.get("close"),
                    "score": item.get("pre_auction_score") or item.get("score"),
                    "target_weight": 0.12,
                    "buy_reference": None,
                    "stop_reference": None,
                    "reason": item.get("reason", "指数动量、舆情和区间学习前夜排序通过"),
                    "operation_guide": "等待次日09:25真实竞价因子确认，不可直接买入",
                }
            )
        return {
            "as_of": payload.get("as_of"),
            "selected_count": len(selections),
            "candidate_count": payload.get("candidate_count"),
            "selections": selections,
            "metrics": {},
            "guidance": [
                "当前仅为前夜观察名单，竞价数据缺失或任一门槛失败时保持现金。",
                "竞价确认后最多5只、单行业最多2只、单只不超过12%、总仓位不超过60%。",
            ],
        }

    @staticmethod
    def _public_parameters(config: dict[str, Any]) -> dict[str, Any]:
        blocked = {"token", "api_key", "api_key_env", "webhook", "secret", "password"}

        def sanitize(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: sanitize(item)
                    for key, item in value.items()
                    if not any(part in key.lower() for part in blocked)
                }
            if isinstance(value, list):
                if len(value) > 20:
                    return {"count": len(value), "preview": [sanitize(item) for item in value[:5]]}
                return [sanitize(item) for item in value]
            return value

        return sanitize(config)

    @staticmethod
    def _read_json(path: Path) -> Optional[dict[str, Any]]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError, TypeError):
            return None

    @classmethod
    def _guidance_by_symbol(cls, path: Path) -> dict[str, str]:
        payload = cls._read_json(path) or {}
        return {
            str(item.get("symbol")): str(item.get("guidance"))
            for item in payload.get("guidance", [])
            if item.get("symbol") and item.get("guidance")
        }

    @staticmethod
    def _percent(value: Any) -> str:
        try:
            return f"{float(value):.2%}"
        except (TypeError, ValueError):
            return "--"

    @staticmethod
    def _percent_points(value: Any) -> str:
        try:
            return f"{float(value):.2f}%"
        except (TypeError, ValueError):
            return "--"

    @staticmethod
    def _log_level(message: str) -> str:
        upper = message.upper()
        if "ERROR" in upper or "TRACEBACK" in upper:
            return "ERROR"
        if "WARNING" in upper or "WARN" in upper:
            return "WARNING"
        return "INFO"

    @staticmethod
    def _copy_run(run: QuantRun) -> QuantRun:
        return QuantRun(
            **{
                **run.to_dict(include_logs=False),
                "logs": [QuantLogEntry(**asdict(entry)) for entry in run.logs],
            }
        )

    @staticmethod
    def _append_log(run: QuantRun, level: str, message: str) -> None:
        sequence = run.logs[-1].sequence + 1 if run.logs else 1
        run.logs.append(QuantLogEntry(sequence, _now().isoformat(), level, message[:4_000]))
        if len(run.logs) > MAX_LOG_LINES:
            run.logs = run.logs[-MAX_LOG_LINES:]

    def _persist_run(self, run: QuantRun) -> None:
        self._run_dir.mkdir(parents=True, exist_ok=True)
        path = self._run_dir / f"{run.run_id}.json"
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(run.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def _load_persisted_runs(self) -> None:
        if not self._run_dir.exists():
            return
        paths = sorted(self._run_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
        for path in paths[:MAX_RUNS]:
            payload = self._read_json(path)
            if payload is None or payload.get("strategy_id") not in self.specs:
                continue
            try:
                logs = [QuantLogEntry(**item) for item in payload.pop("logs", [])]
                run = QuantRun(**payload, logs=logs)
            except (TypeError, ValueError):
                continue
            if run.status in ACTIVE_STATUSES:
                run.status = "failed"
                run.completed_at = _now().isoformat()
                run.error = "Application restarted before the strategy process completed"
                self._append_log(run, "ERROR", run.error)
                self._persist_run(run)
            self._runs[run.run_id] = run

    def _trim_runs_locked(self) -> None:
        if len(self._runs) <= MAX_RUNS:
            return
        removable = sorted(
            (run for run in self._runs.values() if run.status in TERMINAL_STATUSES),
            key=lambda run: run.created_at,
        )
        for run in removable[: max(0, len(self._runs) - MAX_RUNS)]:
            self._runs.pop(run.run_id, None)


def get_quant_strategy_service() -> QuantStrategyService:
    """Return the process-wide quant strategy service."""
    return QuantStrategyService()

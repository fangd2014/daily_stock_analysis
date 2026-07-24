<div align="center">

# 📈 股票智能分析系统

[![GitHub stars](https://img.shields.io/github/stars/ZhuLinsen/daily_stock_analysis?style=social)](https://github.com/ZhuLinsen/daily_stock_analysis/stargazers)
[![CI](https://github.com/ZhuLinsen/daily_stock_analysis/actions/workflows/ci.yml/badge.svg)](https://github.com/ZhuLinsen/daily_stock_analysis/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-Ready-2088FF?logo=github-actions&logoColor=white)](https://github.com/features/actions)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://hub.docker.com/)

> 🤖 基于 AI 大模型的 A股/港股/美股自选股智能分析系统，每日自动分析并推送「决策仪表盘」到企业微信/飞书/Telegram/邮箱

[**功能特性**](#-功能特性) · [**快速开始**](#-快速开始) · [**推送效果**](#-推送效果) · [**完整指南**](docs/full-guide.md) · [**常见问题**](docs/FAQ.md) · [**更新日志**](docs/CHANGELOG.md)

简体中文 | [English](docs/README_EN.md) | [繁體中文](docs/README_CHT.md)

</div>

## 💖 赞助商 (Sponsors)
<div align="center">
  <a href="https://serpapi.com/baidu-search-api?utm_source=github_daily_stock_analysis" target="_blank">
    <img src="./sources/serpapi_banner_zh.png" alt="轻松抓取搜索引擎上的实时金融新闻数据 - SerpApi" height="160">
  </a>
</div>
<br>


## ✨ 功能特性

| 模块 | 功能 | 说明 |
|------|------|------|
| AI | 决策仪表盘 | 一句话核心结论 + 精确买卖点位 + 操作检查清单 |
| 分析 | 多维度分析 | 技术面 + 筹码分布 + 舆情情报 + 实时行情 |
| 市场 | 全球市场 | 支持 A股、港股、美股 |
| 复盘 | 大盘复盘 | 每日市场概览、板块涨跌、北向资金 |
| 推送 | 多渠道通知 | 企业微信、飞书、Telegram、钉钉、邮件、Pushover |
| 自动化 | 定时运行 | GitHub Actions 定时执行，无需服务器 |
| 量化研究 | 分钟级 T+0 回测 | VWAP / 筹码双峰策略、T+1 持仓约束、交易成本、滚动验证与盲测 |

### 技术栈与数据来源

| 类型 | 支持 |
|------|------|
| AI 模型 | Gemini（免费）、OpenAI 兼容、DeepSeek、通义千问、Claude、Ollama |
| 行情数据 | AkShare、Tushare、Pytdx、Baostock、YFinance、TickDB |
| 新闻搜索 | Tavily、SerpAPI、Bocha、Brave |

### 内置交易纪律

| 规则 | 说明 |
|------|------|
| 严禁追高 | 乖离率 > 5% 自动提示风险 |
| 趋势交易 | MA5 > MA10 > MA20 多头排列 |
| 精确点位 | 买入价、止损价、目标价 |
| 检查清单 | 每项条件以「满足 / 注意 / 不满足」标记 |

### T+0 量化回测

仓库提供独立的 A 股分钟级研究模块，内置澜起科技 `688008.SH` 示例。策略使用昨日可卖底仓进行日内卖出和回补，并在撮合中计入佣金、印花税、过户费、滑点、涨跌停与成交量限制。

```bash
# 需要在 .env 配置具备分钟数据权限的 TUSHARE_TOKEN
python -m src.quant.cli --config configs/quant/688008_t0.json fetch
python -m src.quant.cli --config configs/quant/688008_t0.json optimize
python -m src.quant.cli --config configs/quant/688008_t0.json backtest --holdout

# 离线单元与回归测试
./test.sh quant
```

缓存写入 `data/quant_cache/`，报告写入 `reports/quant/`。回测结果仅用于研究，不构成投资建议，也不代表未来收益。

### TickDB 四因子选股

新增指数动量、舆情语义、区间学习与特征归因、竞价异动联动四因子选股。策略采用“前夜观察名单 +
次日 9:25 真实竞价确认”的两阶段流程，最多持有 5 只、单行业最多 2 只；没有真实 A 股竞价盘口时
不会产生自动买入信号。正式全市场调用需配置 `TICKDB_API_KEY`；除进程环境变量和 `.env` 外，客户端
也会安全读取 `~/.zshrc` 或 `~/zshrc` 中的字面量赋值，但不会执行 shell 配置。

```bash
python -m src.quant.tickdb_factor_snapshot --symbols 600519.SH,601318.SH
python -m src.quant.four_factor_runner \
  --input-dir data/quant_four_factor/input \
  --as-of 2026-07-24T20:00:00
```

已有全A日线/资金流收盘缓存时，可直接运行晚间实时筛选。该命令会构造行业等权指数、训练严格时点
分位模型、搜索候选新闻，并使用TickDB复核行情；输出仍需下一交易日9:25真实竞价确认：

```bash
source scripts/load_deepseek_env.zsh
python -m src.quant.four_factor_live --as-of 2026-07-24T20:00:00
```

公式、输入字段、风控和 TickDB 能力边界见[四因子策略说明](docs/FOUR_FACTOR_STRATEGY.md)。
📡 数据由 TickDB.ai 提供

### ETF动量轮动模拟盘

ETF策略从15只宽基、行业、海外、黄金和国债ETF中做周频横截面轮动。每周最后一个交易日收盘后按
20/60/120/250日
动量加权，扣除60日波动率和120日回撤惩罚，只保留收盘高于MA120且120日收益为正的ETF。组合最多
持有3只、每类资产最多1只、单只20%、总仓位最高60%；开盘高于前收3%时不追高。单只固定止损7%，
从持有期高点回撤6%退出；组合回撤8%降至30%仓位，回撤12%清仓并冷静20个交易日。

历史价格和基金复权因子来自Tushare，交易日历来自TickDB。回测按信号后首个交易日开盘成交，计入
0.01%佣金、最低5元佣金和0.03%滑点。运行命令会生成权益、成交、信号及Markdown/JSON报告：

```bash
# 固定区间研究，不推送
python -m src.quant.etf_momentum \
  --config configs/quant/etf_momentum_rotation.json research

# 使用滚动最近一年区间，生成下周计划并推送飞书
python -m src.quant.etf_momentum \
  --config configs/quant/etf_momentum_rotation.json weekly

# 安装每周日20:00复盘任务
ETF_MOMENTUM_PYTHON="$(command -v python3)" \
  ./scripts/install_etf_momentum_cron.sh

# 五年前复权日线每日重算回测
python -m src.quant.etf_momentum \
  --config configs/quant/etf_momentum_5y_daily_proxy.json research
```

需在`.env`配置`TUSHARE_TOKEN`和`FEISHU_WEBHOOK_URL`，并提供`TICKDB_API_KEY`（可由客户端安全读取
`~/.zshrc`中的字面量赋值）。报告保存在`reports/quant/etf_momentum/`，日志位于
`logs/quant_etf/weekly.log`。该策略仅用于100万元模拟盘研究，不连接券商，不保证未来收益。

五年报告采用真实前复权日线，严格按“每日收盘重算、下一交易日开盘成交”执行。回测区间
2021-07-26至2026-07-24，总收益-2.44%、
年化-0.49%、最大回撤16.60%、费用2.12万元，说明提高全量调仓频率带来的换手成本会破坏原周频优势。
失败结果保存在`reports/quant/etf_momentum_5y_daily_proxy/`，不会用一年结果替代五年结论。
同一日频规则在最近一年得到总收益11.76%、最大回撤11.15%，但不能推翻五年长期结果。

改为每周最后一个交易日重算后，五年总收益5.09%、年化1.00%、最大回撤14.06%，成交降至748笔，
费用降至6927元；相比日频有所改善，但仍未达到长期收益目标。周频完整报告保存在
`reports/quant/etf_momentum_5y_weekly/`。最近一年周频收益17.83%、最大回撤9.76%，短期结果不能替代
五年结论。

📡 数据由 TickDB.ai 提供

若 Tushare 分钟接口频率不足，可将 CSV/Parquet 路径填入配置的 `data.local_path`，并把 `data.source` 改为 `local`；文件至少需包含时间、OHLC 和成交量列。
示例配置默认每次只下载一个 6 个月分块，以适配低频账户并支持断点续跑；可按账户权限调整 `max_chunks_per_run`，设为 `0` 表示单次拉取全部缺失分块。

也可使用 PyTDX 作为分钟行情主源：

```bash
python -m src.quant.cli --config configs/quant/quant_research_tdx.json fetch
```

该配置会并发探测 PyTDX 自带行情节点，按延迟排序，按每页最多 800 根 K 线向历史分页，并在节点中断时从同一偏移量续传。数据按月写入 Parquet；活动区间采用三天重叠增量刷新。写入前会检查重复时间戳、OHLC 合法性、每个交易日 K 线完整度，并用 Tushare 日线校准成交量/成交额单位和交叉核对收盘价。质量报告位于缓存目录的 `quality.json`，任何检查失败都会禁止该 PyTDX 缓存进入优化。

公开 PyTDX 节点的分钟历史保留期有限且可能变化，示例从当前节点可覆盖的 `2024-07-02` 开始，并使用 12 个月训练、3 个月滚动验证和 2026 年第二季度独立盲测。更早的分钟历史需由 Tushare 或本地授权数据补齐，不会用缺失区间伪造长期回测。

`data.fallback_source` 可设为 `tushare`、`local` 或 `none`。降级不是静默的，实际使用的数据源和主源错误会写入同目录的 `active_source.json`。PyTDX 使用原始 TCP 协议；若本机网络或代理不允许访问行情节点，应开放相应出站连接，或显式使用上述降级源。Tushare 交叉校验仍需在 `.env` 配置 `TUSHARE_TOKEN`。

最近三年5分钟行情可使用四源回填器，顺序固定为 Tushare、AKShare、Baostock、tdx2db：

```bash
python -m src.quant.minute_history_backfill \
  --config configs/quant/minute_history_3y.json

# 查看持续回填日志和最新统计
tail -f logs/quant_backfill/minute_history_3y.log
cat reports/quant/minute_history_3y/minute_history_statistics.md
```

默认区间为2023-07-24至2026-07-24，统一为5分钟不复权行情，数据池包含当前五股组合及
`688008.SH`。每个数据源独立缓存，合并时按上述优先级保留同一时间戳的第一条记录；统计报告包含
K线数、交易日、起止时间、重复、OHLC异常、每日K线众数和完整日比例。Tushare当前账户分钟接口限频
为每小时1次，因此安装脚本会在每小时11分只下载一个半年分区并原子落盘，任务中断后继续缺失分区：

```bash
MINUTE_HISTORY_PYTHON="$(command -v python3)" \
  ./scripts/install_minute_history_backfill_cron.sh
```

AKShare和Baostock是网络接口，失败会记录真实错误并继续降级；tdx2db不是在线行情源，必须先在通达信
执行盘后分钟数据下载并配置 `TDX_PATH`，使其目录下存在 `vipdoc/{sh,sz}/fzline/*.lc5`。完整环境探测
和当前覆盖见[分钟数据报告](docs/MINUTE_DATA_REPORT.md)。

36 个月严格样本外研究使用全 A 股日线组合配置：

```bash
python -m src.quant.cli --config configs/quant/quant_research_36m.json fetch

# 首次部署可显式提高单次回填上限；缓存按交易日幂等续传
python -m src.quant.cli --config configs/quant/quant_research_36m.json fetch --max-days 1200

# 运行固定五股预序组合并用独立验证器重算结果
python -m src.quant.cli --config configs/quant/quant_research_36m.json prequential
python -m src.quant.validate_research \
  --config configs/quant/quant_research_36m.json \
  --output .omx/specs/autoresearch-chip-peak-quant/result.json
```

该配置按交易日缓存 `2021-07-01` 至 `2026-06-30` 的全 A 股日线和涨跌停价，共约 1210 个交易日；每次默认增量回填 100 日。历史候选池优先使用 Tushare `bak_basic` 快照；当前 Token 无该权限时，系统会用包含退市股票的 `stock_basic` 上市/退市日期和分页完整的 `namechange` 历史名称重建，并在快照中记录实际来源。任何日线横截面、历史成员或名称变更数据不完整都会停止研究，不能拿今天的上市名单回溯替代。

验收期固定为 `2023-07` 至 `2026-06` 共 36 个月。研究器支持以前 18 个月训练和 6 个月验证做逐月参数锁定，也支持完全固定的预注册规格。当前配置使用消融后最简单的固定规则：`chip_low_risk` 因子、低峰区间位置不高于 0.28、60% 总仓位、中峰止盈、6% 单股止损和最长 15 个交易日持有，不允许逐月关闭风控。组合每月从历史全 A 股候选池中选择恰好 5 只当日可买股票，持仓不得超过 5 只；月初不足 5 只时持有现金并在月内继续等待，等待期收益计为零。`688008.SH` 与其他股票使用完全相同的入选规则，但单股结果不能作为最终验收证据。

独立验证器会读取 `research_manifest.json`，逐月核对参数截止日、历史股票池、24 个月样本窗、5 只股票、逐股可买状态、信号与延迟成交时间，以及行情和股票池快照摘要；同时从组合权益重新计算年化收益、月收益和最大回撤，并验证月收益 CSV 与权益完全一致。硬验收要求连续 36 个样本外月、月收益中位数不低于 8%、年化收益率不低于 15%、最大回撤不高于 15%；年化 20% 和最大回撤 10% 是优选目标，不是上下限反向惩罚。8%月收益复利约对应152%年化，只是高难度研究门槛，不是收益保证；验证失败时必须保留失败结论。

截至 2026-07-24，三轮完整研究均未通过收益门槛。最终固定风控规格在 36 个连续月份中得到年化 1.01%、月收益中位数 0.17%、最大回撤 11.35%，仅通过回撤门槛；[完整研究报告](docs/QUANT_RESEARCH_REPORT.md)和 `.omx/specs/autoresearch-chip-peak-quant/result.json` 均保留失败结论。该固定规格是在观察前两轮及同区间消融后确定，因此属于开发样本上的候选，不能视为新的未触碰盲测，也不得据此宣称可稳定实现月收益 8%。

筹码峰策略继续保留，同时新增三套不依赖筹码峰的预注册选股器：时点质量价值、行业中军动量、
资金流确认反转。估值和资金流按月末交易日缓存，财务指标严格使用公告日不晚于特征截止日的记录；
三套策略沿用相同的36个月、100万元、固定5股、交易成本和独立验证口径。

```bash
# 首次拉取36个月的估值、资金流和公告时点财务快照
python -m src.quant.cli --config configs/quant/quant_research_36m.json fetch-factors

# 运行三套策略并分别生成审计清单、权益曲线和验证结果
python -m src.quant.cli --config configs/quant/quant_research_36m.json tournament
```

三套策略均未通过门槛：质量价值年化1.22%、月收益中位数-0.81%、最大回撤24.84%；行业中军动量
年化-8.17%、月收益中位数-0.56%、最大回撤32.94%；资金流确认反转年化-7.02%、月收益中位数
-0.58%、最大回撤24.94%。结果保存在
`reports/quant/chip_peak_all_a_36m_oos_v3_fixed_risk/strategy_tournament/`，失败结果不会被隐藏或替换。

若账户的 `stk_mins` 权限为每小时一次，可安装幂等回填任务：

```bash
QUANT_BACKFILL_PYTHON="$(command -v python3)" ./scripts/install_quant_backfill_cron.sh
```

任务每天 `00:17` 和 `12:17` 运行。全 A 研究配置每次最多补齐 100 个交易日的日线和涨跌停价，已缓存交易日不会重复请求；单股分钟示例仍沿用各自的分钟分区逻辑。

策略支持基于前一交易日趋势和波动率的动态底仓开关，以及高抛/低吸方向和 VWAP Z-score 确认因子。所有信号只使用前一日或当前已收盘 K 线；这些因子必须先通过滚动验证，不能直接根据盲测期表现启用。近期诊断表明动态底仓可显著降低回撤，但当前双峰配对放大仓位后成本后收益为负，因此尚未达到最终目标。

#### 筹码双峰高抛低吸策略

`configs/quant/688008_chip_double_peak.json` 仅保留为单股功能示例，不参与最终全 A 组合验证。它只使用信号日前 60 个交易日的成交量价格分布，在两个主峰至少相距 8%、峰间谷值不高于较弱峰 70% 时确认双峰。股价位于两峰之间且进入区间下方 28% 时低吸，进入上方 28% 时高抛，回到 50% 中轴平仓。

```bash
python -m src.quant.cli --config configs/quant/688008_chip_double_peak.json fetch
python -m src.quant.cli --config configs/quant/688008_chip_double_peak.json backtest

# 初始化 100 万元独立模拟账户
python -m src.quant.paper --config configs/quant/688008_chip_double_peak_paper.json init
python -m src.quant.paper --config configs/quant/688008_chip_double_peak_paper.json status

# 如需通过独立任务与现有模拟盘并行运行
QUANT_PAPER_CONFIG=configs/quant/688008_chip_double_peak_paper.json \
  QUANT_PAPER_TASK_ID=chip-double-peak \
  QUANT_PAPER_LOG=logs/quant_paper/chip_double_peak.log \
  QUANT_PAPER_PYTHON="$(command -v python3)" ./scripts/install_quant_paper_cron.sh
```

示例采用 30% 底仓，每组交易使用底仓的 30%（约账户权益的 9%），每日最多一组；单次最长持有 24 根 5 分钟 K 线并于收盘前强制恢复底仓。普通示例的研究验收目标设为年化收益 10%、最大回撤 10%、Calmar 1.2；`quant_research_tdx.json` 另行保留月均 10%、最大回撤 10% 的高门槛，只能由无未来数据的滚动验证与独立盲测判定。所有目标均是模拟研究门槛，不是收益或回撤保证。建议至少运行 6 个月、覆盖 100 组配对交易后再评估参数。

#### 全 A 双峰五股组合

组合模拟盘从每晚全 A 双峰筛选结果中取排名最靠前的 5 只可买股票，每只分配 20 万元子账户、目标底仓 6 万元，合计 100 万元资金和约 30% 目标仓位。候选不限行业；当前成分来自 `2026-07-22` 的历史筛选结果，后续包括 `688008.SH` 在内的所有股票都按相同条件参与调仓。

```bash
# 初始化并查看 100 万元、固定 5 只、目标总仓位 30% 的组合模拟盘
python -m src.quant.portfolio_paper --config configs/quant/tech_chip_portfolio_paper.json init
python -m src.quant.portfolio_paper --config configs/quant/tech_chip_portfolio_paper.json status

# 使用晚间筛选结果前5只做三个月日线近似回测
python -m src.quant.daily_portfolio_backtest \
  --selection reports/quant/daily_chip_screen/selection_20260722.json \
  --template-config configs/quant/688008_chip_double_peak.json \
  --start 2026-07-23 --end 2026-10-22 \
  --output-dir reports/quant/all_a_chip_5_3m

# 手动生成当日收盘复盘
python -m src.quant.daily_review \
  --config configs/quant/tech_chip_portfolio_paper.json \
  --at 2026-07-23T15:25:00+08:00

# 与原有模拟盘并行安装，使用相同任务 ID 可幂等更新组合成分
QUANT_PAPER_MODULE=src.quant.portfolio_paper \
  QUANT_DAILY_REVIEW_MODULE=src.quant.daily_review \
  QUANT_PAPER_CONFIG=configs/quant/tech_chip_portfolio_paper.json \
  QUANT_PAPER_TASK_ID=chip-double-peak \
  QUANT_PAPER_LOG=logs/quant_paper/tech_chip_portfolio.log \
  QUANT_PAPER_PYTHON="$(command -v python3)" ./scripts/install_quant_paper_cron.sh
```

组合模拟盘为每只股票维护独立资金、底仓、T+1 可卖数量和双峰信号，再汇总为 100 万元组合权益。由于 A 股按 100 股整手交易，实际建仓仓位允许在 30% 附近最多偏差 1 个百分点；不会为了凑足仓位买入未通过双峰筛选的股票。持仓池保持 5 只且绝不超过 5 只，正式调仓仍需在调仓日重新确认可买条件。首次建仓还会在下一交易日逐个检查涨停、相对昨收涨幅不超过 3%，以及实时价格仍处于选股时双峰的低位买入区；条件失效时等待，不追高成交。

三个月回测采用选股日以前的历史数据确认资格，以当日收盘信号、下一交易日开盘成交作日线近似。日线近似用于验证逻辑，不等同于5分钟模拟盘的精确成交结果，也不替代36个月逐月预序验收。

启用 `QUANT_DAILY_REVIEW_MODULE` 后，cron 会在每个工作日 15:25 汇总成交、费用、规则错误和策略内亏损，报告写入 `reports/quant_paper/all_a_chip_portfolio/daily_reviews/`。少于30组配对时只收集样本；达到门槛后仅写入 `optimization_queue.json` 作为候选，任何参数都必须通过滚动验证后人工启用，不会因单日盈亏自动修改。

DeepSeek复盘任务每天20:00读取上述确定性复盘、逐笔成交、拒单、当前持仓和组合权益，生成操作评价并推送飞书；每周五21:00再汇总当周收益、最大回撤、费用、错误操作和策略内亏损，生成周复盘并推送。模型只提出待验证假设，不会直接修改策略参数。DeepSeek密钥从 `~/.zshrc` 的 `DEEPSEEK_API_KEY` 定向读取，飞书Webhook继续从项目 `.env` 加载。

```bash
DEEPSEEK_REVIEW_PYTHON="$(command -v python3)" \
  ./scripts/install_deepseek_review_cron.sh

# 手动验证每日或每周复盘
/bin/zsh scripts/run_deepseek_review.zsh "$(command -v python3)" \
  configs/quant/tech_chip_portfolio_paper.json daily
/bin/zsh scripts/run_deepseek_review.zsh "$(command -v python3)" \
  configs/quant/tech_chip_portfolio_paper.json weekly
```

AI复盘报告保存在 `reports/quant_paper/all_a_chip_portfolio/deepseek_reviews/`，每日和每周飞书推送分别按日期幂等，失败时保留报告供下一次重试。

#### 每晚筹码双峰10股筛选与次日指导

`configs/quant/daily_chip_screen.json` 面向全A股做独立晚间筛选。筹码双峰仅使用信号日以前60个交易日；信号日要求股票仍处于双峰之间且峰间位置不高于45%、所属行业成分股平均涨幅为正、大单与特大单合计净买入为正。合格股票按收盘价距低筹码峰的百分比由近到远排序，固定选择10只；不足10只时任务报错，不使用不合格股票补位。

```bash
# 只运行筛选，不调用AI或推送
python -m src.quant.daily_chip_screener \
  --config configs/quant/daily_chip_screen.json screen

# 完整运行：筛选10只、DeepSeek生成次日指导、推送飞书
python -m src.quant.daily_chip_screener \
  --config configs/quant/daily_chip_screen.json run

# 安装每个工作日20:00的独立定时任务
DAILY_CHIP_SCREEN_PYTHON="$(command -v python3)" \
  ./scripts/install_daily_chip_screen_cron.sh
```

完整任务需要配置 `TUSHARE_TOKEN`、`DEEPSEEK_API_KEY`（也可复用 `OPENAI_API_KEY`）和 `FEISHU_WEBHOOK_URL`。项目配置仍从 `.env` 加载；通过上述脚本安装的20:00定时任务还会从 `~/.zshrc` 中安全提取 `DEEPSEEK_API_KEY` 的 `export` 赋值，但不会执行整个 shell 配置，因此可以复用用户级环境变量而无需把密钥复制到项目文件或 cron 文本。DeepSeek 每只输出下一交易日操作结论、触发区间、止损失效条件及止盈减仓条件；报告写入 `reports/quant/daily_chip_screen/` 后再推送，密钥和 Webhook 不会写入报告或日志。同一交易日只成功推送一次，节假日不会重复推送上一交易日结果；飞书失败时会保留指导供重试。该指导仅供模拟盘研究。

#### 每晚热门板块中军短线筛选

`configs/quant/hot_sector_screen.json` 是独立于五股组合的短线观察列表。任务先要求行业当日平均上涨至少
0.8%、上涨家数占比至少60%、成交额活跃且20日趋势向上，再从热度前5个行业中寻找成交额位于板块
前40%的中军。个股还必须满足20日均额不低于3亿元、放量、`close > MA20 > MA60`、主力净流入为正、
涨幅不超过7%且距离MA20不超过8%。每行业最多3只，全市场最多10只；不足10只时保留现金和观察，
不会用不合格股票补位。

```bash
# 只生成确定性筛选报告
python -m src.quant.hot_sector_screener \
  --config configs/quant/hot_sector_screen.json screen

# DeepSeek生成1至5个交易日操作计划并推送飞书
/bin/zsh scripts/run_hot_sector_screen.zsh \
  "$(command -v python3)" configs/quant/hot_sector_screen.json

# 安装工作日20:05任务
HOT_SECTOR_SCREEN_PYTHON="$(command -v python3)" \
  ./scripts/install_hot_sector_screen_cron.sh
```

推送内容包含板块热度、选股理由、观察买入区、高开3%放弃条件、约5%硬止损、6%/10%两档止盈和
最长5日持有期；板块跌出热度榜即取消计划。DeepSeek密钥由启动脚本从 `~/.zshrc` 定向读取，飞书
Webhook从 `.env` 读取，成功推送按交易日幂等。2026-07-24真实试跑因市场普跌没有合格行业，系统已
正确推送空名单和观望结论，没有强行凑票。

筛选过程会在控制台打印数据规模、板块门槛结果、热门板块排名、个股淘汰原因汇总、最终候选排名、
DeepSeek进度和飞书状态。定时任务的完整输出保存在 `logs/quant_screen/hot_sector_screen.log`。

### T+0 模拟盘

`configs/quant/688008_paper.json` 提供 100 万元澜起科技模拟账户，起始日为 `2026-07-21`。系统每 5 分钟读取腾讯行情并持久化账户、K 线和模拟成交；首日按 60% 资金建立底仓，买入股份在下一交易日结算后才可用于 T+0。该流程不会连接券商或提交真实委托。

```bash
# 初始化并查看账户
python -m src.quant.paper --config configs/quant/688008_paper.json init
python -m src.quant.paper --config configs/quant/688008_paper.json status

# 安装幂等的本机定时任务：交易时段轮询，周五 15:20 生成周报
QUANT_PAPER_PYTHON="$(command -v python3)" ./scripts/install_quant_paper_cron.sh
```

运行状态保存在 `data/quant_paper/688008/`，日志位于 `logs/quant_paper/cron.log`，周报写入 `reports/quant_paper/688008/week_*.md` 并同步更新 `latest.md`。收益目标不构成保证，评估应以持续模拟结果为准。

## 🚀 快速开始

### 方式一：GitHub Actions（推荐）

> 5 分钟完成部署，零成本，无需服务器。


#### 1. Fork 本仓库

点击右上角 `Fork` 按钮（顺便点个 Star⭐ 支持一下）

#### 2. 配置 Secrets

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`

**AI 模型配置（二选一）**

| Secret 名称 | 说明 | 必填 |
|------------|------|:----:|
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/) 获取免费 Key | ✅* |
| `OPENAI_API_KEY` | OpenAI 兼容 API Key（支持 DeepSeek、通义千问等） | 可选 |
| `OPENAI_BASE_URL` | OpenAI 兼容 API 地址（如 `https://api.deepseek.com/v1`） | 可选 |
| `OPENAI_MODEL` | 模型名称（如 `deepseek-chat`） | 可选 |

> 注：`GEMINI_API_KEY` 和 `OPENAI_API_KEY` 至少配置一个

<details>
<summary><b>通知渠道配置</b>（点击展开，至少配置一个）</summary>


| Secret 名称 | 说明 | 必填 |
|------------|------|:----:|
| `WECHAT_WEBHOOK_URL` | 企业微信 Webhook URL | 可选 |
| `FEISHU_WEBHOOK_URL` | 飞书 Webhook URL | 可选 |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token（@BotFather 获取） | 可选 |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID | 可选 |
| `TELEGRAM_MESSAGE_THREAD_ID` | Telegram Topic ID (用于发送到子话题) | 可选 |
| `EMAIL_SENDER` | 发件人邮箱（如 `xxx@qq.com`） | 可选 |
| `EMAIL_PASSWORD` | 邮箱授权码（非登录密码） | 可选 |
| `EMAIL_RECEIVERS` | 收件人邮箱（多个用逗号分隔，留空则发给自己） | 可选 |
| `EMAIL_SENDER_NAME` | 邮件发件人显示名称（默认：daily_stock_analysis股票分析助手） | 可选 |
| `PUSHPLUS_TOKEN` | PushPlus Token（[获取地址](https://www.pushplus.plus)，国内推送服务） | 可选 |
| `SERVERCHAN3_SENDKEY` | Server酱³ Sendkey（[获取地址](https://sc3.ft07.com/)，手机APP推送服务） | 可选 |
| `CUSTOM_WEBHOOK_URLS` | 自定义 Webhook（支持钉钉等，多个用逗号分隔） | 可选 |
| `CUSTOM_WEBHOOK_BEARER_TOKEN` | 自定义 Webhook 的 Bearer Token（用于需要认证的 Webhook） | 可选 |
| `SINGLE_STOCK_NOTIFY` | 单股推送模式：设为 `true` 则每分析完一只股票立即推送 | 可选 |
| `REPORT_TYPE` | 报告类型：`simple`(精简) 或 `full`(完整)，Docker环境推荐设为 `full` | 可选 |
| `ANALYSIS_DELAY` | 个股分析和大盘分析之间的延迟（秒），避免API限流，如 `10` | 可选 |

> 至少配置一个渠道，配置多个则同时推送。更多配置请参考 [完整指南](docs/full-guide.md)

</details>

**其他配置**

| Secret 名称 | 说明 | 必填 |
|------------|------|:----:|
| `STOCK_LIST` | 自选股代码，如 `600519,hk00700,AAPL,TSLA` | ✅ |
| `TAVILY_API_KEYS` | [Tavily](https://tavily.com/) 搜索 API（新闻搜索） | 推荐 |
| `SERPAPI_API_KEYS` | [SerpAPI](https://serpapi.com/baidu-search-api?utm_source=github_daily_stock_analysis) 全渠道搜索 | 可选 |
| `BOCHA_API_KEYS` | [博查搜索](https://open.bocha.cn/) Web Search API（中文搜索优化，支持AI摘要，多个key用逗号分隔） | 可选 |
| `BRAVE_API_KEYS` | [Brave Search](https://brave.com/search/api/) API（隐私优先，美股优化，多个key用逗号分隔） | 可选 |
| `TUSHARE_TOKEN` | [Tushare Pro](https://tushare.pro/weborder/#/login?reg=834638 ) Token | 可选 |
| `TUSHARE_API_URL` | Tushare Pro API 地址，默认 `http://api.tushare.pro` | 可选 |
| `WECHAT_MSG_TYPE` | 企微消息类型，默认 markdown，支持配置 text 类型，发送纯 markdown 文本 | 可选 |

#### 3. 启用 Actions

`Actions` 标签 → `I understand my workflows, go ahead and enable them`

#### 4. 手动测试

`Actions` → `每日股票分析` → `Run workflow` → `Run workflow`

#### 完成

默认每个**工作日 18:00（北京时间）**自动执行，也可手动触发

### 方式二：本地运行 / Docker 部署

```bash
# 克隆项目
git clone https://github.com/ZhuLinsen/daily_stock_analysis.git && cd daily_stock_analysis

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env && vim .env

# 运行分析
python main.py
```

> Docker 部署、定时任务配置请参考 [完整指南](docs/full-guide.md)

## 📱 推送效果

![运行效果演示](./sources/all_2026-01-13_221547.gif)

### 决策仪表盘
```
📊 2026-01-10 决策仪表盘
3只股票 | 🟢买入:1 🟡观望:2 🔴卖出:0

🟢 买入 | 贵州茅台(600519)
📌 缩量回踩MA5支撑，乖离率1.2%处于最佳买点
💰 狙击: 买入1800 | 止损1750 | 目标1900
✅多头排列 ✅乖离安全 ✅量能配合

🟡 观望 | 宁德时代(300750)
📌 乖离率7.8%超过5%警戒线，严禁追高
⚠️ 等待回调至MA5附近再考虑

---
生成时间: 18:00
```

### 大盘复盘

![大盘复盘推送效果](./sources/dapan_2026-01-13_22-14-52.png)

```
🎯 2026-01-10 大盘复盘

📊 主要指数
- 上证指数: 3250.12 (🟢+0.85%)
- 深证成指: 10521.36 (🟢+1.02%)
- 创业板指: 2156.78 (🟢+1.35%)

📈 市场概况
上涨: 3920 | 下跌: 1349 | 涨停: 155 | 跌停: 3

🔥 板块表现
领涨: 互联网服务、文化传媒、小金属
领跌: 保险、航空机场、光伏设备
```
## ⚙️ 配置说明

> 📖 完整环境变量、定时任务配置请参考 [完整配置指南](docs/full-guide.md)


## 🖥️ Web 界面

![img.png](sources/fastapi_server.png)

包含股票分析和独立的量化选股工作台。访问 `/quant` 可以查看最近的筹码双峰、热门板块中军、
四因子时点选股和ETF动量轮动结果，并完成以下操作：

- 查看因子、买卖条件、风控规则和脱敏后的完整策略参数
- 手动启动已注册的模拟策略；页面不会接受任意系统命令，也不会连接券商
- 实时查看数据加载、条件过滤、排序和报告生成日志
- 统一展示最新选股、买入参考、止损参考、目标仓位和逐股操作说明
- 策略运行记录与日志保存在 `reports/quant/web_runs/`，服务重启后仍可审计

### 启动方式

1. **编译前端** (首次运行需要)
   ```bash
   cd ./apps/dsa-web
   npm install && npm run build
   cd ../..
   ```

2. **启动服务**
   ```bash
   python main.py --webui       # 启动 Web 界面 + 执行定时分析
   python main.py --webui-only  # 仅启动 Web 界面
   ```

访问 `http://127.0.0.1:8000` 使用股票分析，访问 `http://127.0.0.1:8000/quant` 使用量化选股工作台。

量化页面后端接口统一位于 `/api/v1/quant`：策略目录使用 `GET /strategies`，手动启动使用
`POST /runs`，运行状态与日志使用 `GET /runs/{run_id}`。只有后端登记的白名单策略可以启动；
API返回的策略参数会移除Token、API Key和Webhook等敏感配置。

> 也可以使用 `python main.py --serve` (等效命令)

## 🗺️ Roadmap

查看已支持的功能和未来规划：[更新日志](docs/CHANGELOG.md)

> 有建议？欢迎 [提交 Issue](https://github.com/ZhuLinsen/daily_stock_analysis/issues)


---

## ☕ 支持项目

如果本项目对你有帮助，欢迎支持项目的持续维护与迭代，感谢支持 🙏  
赞赏可备注联系方式，祝股市长虹

| 支付宝 (Alipay) | 微信支付 (WeChat) | Ko-fi |
| :---: | :---: | :---: |
| <img src="./sources/alipay.jpg" width="200" alt="Alipay"> | <img src="./sources/wechatpay.jpg" width="200" alt="WeChat Pay"> | <a href="https://ko-fi.com/mumu157" target="_blank"><img src="./sources/ko-fi.png" width="200" alt="Ko-fi"></a> |

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

详见 [贡献指南](docs/CONTRIBUTING.md)

## 📄 License
[MIT License](LICENSE) © 2026 ZhuLinsen

如果你在项目中使用或基于本项目进行二次开发，
非常欢迎在 README 或文档中注明来源并附上本仓库链接。
这将有助于项目的持续维护和社区发展。

## 📬 联系与合作
- GitHub Issues：[提交 Issue](https://github.com/ZhuLinsen/daily_stock_analysis/issues)

## ⭐ Star History
**如果觉得有用，请给个 ⭐ Star 支持一下！**

<a href="https://star-history.com/#ZhuLinsen/daily_stock_analysis&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=ZhuLinsen/daily_stock_analysis&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=ZhuLinsen/daily_stock_analysis&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=ZhuLinsen/daily_stock_analysis&type=Date" />
 </picture>
</a>

## ⚠️ 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。股市有风险，投资需谨慎。作者不对使用本项目产生的任何损失负责。

---

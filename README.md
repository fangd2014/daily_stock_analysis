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
| 行情数据 | AkShare、Tushare、Pytdx、Baostock、YFinance |
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
若 Tushare 分钟接口频率不足，可将 CSV/Parquet 路径填入配置的 `data.local_path`，并把 `data.source` 改为 `local`；文件至少需包含时间、OHLC 和成交量列。
示例配置默认每次只下载一个 6 个月分块，以适配低频账户并支持断点续跑；可按账户权限调整 `max_chunks_per_run`，设为 `0` 表示单次拉取全部缺失分块。

也可使用 PyTDX 作为分钟行情主源：

```bash
python -m src.quant.cli --config configs/quant/quant_research_tdx.json fetch
```

该配置会并发探测 PyTDX 自带行情节点，按延迟排序，按每页最多 800 根 K 线向历史分页，并在节点中断时从同一偏移量续传。数据按月写入 Parquet；活动区间采用三天重叠增量刷新。写入前会检查重复时间戳、OHLC 合法性、每个交易日 K 线完整度，并用 Tushare 日线校准成交量/成交额单位和交叉核对收盘价。质量报告位于缓存目录的 `quality.json`，任何检查失败都会禁止该 PyTDX 缓存进入优化。

公开 PyTDX 节点的分钟历史保留期有限且可能变化，示例从当前节点可覆盖的 `2024-07-02` 开始，并使用 12 个月训练、3 个月滚动验证和 2026 年第二季度独立盲测。更早的分钟历史需由 Tushare 或本地授权数据补齐，不会用缺失区间伪造长期回测。

`data.fallback_source` 可设为 `tushare`、`local` 或 `none`。降级不是静默的，实际使用的数据源和主源错误会写入同目录的 `active_source.json`。PyTDX 使用原始 TCP 协议；若本机网络或代理不允许访问行情节点，应开放相应出站连接，或显式使用上述降级源。Tushare 交叉校验仍需在 `.env` 配置 `TUSHARE_TOKEN`。

#### 筹码双峰高抛低吸策略

`configs/quant/688008_chip_double_peak.json` 是独立的筹码双峰策略示例。它只使用信号日前 60 个交易日的成交量价格分布，在两个主峰至少相距 8%、峰间谷值不高于较弱峰 70% 时确认双峰。股价位于两峰之间且进入区间下方 28% 时低吸，进入上方 28% 时高抛，回到 50% 中轴平仓。

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

#### 科技龙头双峰组合

`configs/quant/tech_head_universe.json` 定义 20 只半导体、AI 算力、光通信、消费电子和工业软件龙头候选。筛选器按前 60 个交易日确认双峰，要求股价仍在两峰之间、市值不低于 500 亿元且近 20 日平均成交额不低于 5 亿元。首次选股和调股还要求峰间位置不高于 45%，只从当日允许新建仓的股票中按总市值选择 3 只；日内低吸继续使用更严格的 28% 阈值。某只失去资格时，下一只满足全部条件的股票进入筛选结果补位，持仓池不超过 3 只。

```bash
# 更新筛选结果
python -m src.quant.tech_screener --config configs/quant/tech_head_universe.json

# 初始化并查看 100 万元、固定 3 只、目标总仓位 30% 的组合模拟盘
python -m src.quant.portfolio_paper --config configs/quant/tech_chip_portfolio_paper.json init
python -m src.quant.portfolio_paper --config configs/quant/tech_chip_portfolio_paper.json status

# 使用选股日已满足买入条件的3只股票做三个月无未来数据回测
python -m src.quant.daily_portfolio_backtest \
  --selection reports/quant/tech_chip_screen/selection_20260422.json \
  --template-config configs/quant/688008_chip_double_peak.json \
  --start 2026-04-23 --end 2026-07-22 \
  --output-dir reports/quant/tech_chip_3m_20260423_20260722

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

组合模拟盘为每只股票维护独立资金、底仓、T+1 可卖数量和双峰信号，再汇总为 100 万元组合权益。由于 A 股按 100 股整手交易，实际建仓仓位允许在 30% 附近最多偏差 1 个百分点；不会为了凑足仓位买入未通过双峰筛选的股票。持仓池按条件保持 3 只，建议每月执行一次成分复核，避免每日换股。

三个月回测采用选股日以前的历史数据确认资格，以当日收盘信号、下一交易日开盘成交作日线近似。完整报告位于 `reports/quant/tech_chip_3m_20260423_20260722/report.md`。日线近似用于验证逻辑，不等同于5分钟模拟盘的精确成交结果。

启用 `QUANT_DAILY_REVIEW_MODULE` 后，cron 会在每个工作日 15:25 汇总成交、费用、规则错误和策略内亏损，报告写入 `reports/quant_paper/tech_chip_portfolio/daily_reviews/`。少于30组配对时只收集样本；达到门槛后仅写入 `optimization_queue.json` 作为候选，任何参数都必须通过滚动验证后人工启用，不会因单日盈亏自动修改。

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

完整任务需要在 `.env` 配置 `TUSHARE_TOKEN`、`DEEPSEEK_API_KEY`（也可复用 `OPENAI_API_KEY`）和 `FEISHU_WEBHOOK_URL`。DeepSeek 每只输出下一交易日操作结论、触发区间、止损失效条件及止盈减仓条件；报告写入 `reports/quant/daily_chip_screen/` 后再推送，Webhook 不会写入报告或日志。同一交易日只成功推送一次，节假日不会重复推送上一交易日结果；飞书失败时会保留指导供重试。该指导仅供模拟盘研究。

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

包含完整的配置管理、任务监控和手动分析功能。

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

访问 `http://127.0.0.1:8000` 即可使用。

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

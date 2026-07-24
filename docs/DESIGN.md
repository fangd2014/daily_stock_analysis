# Detailed Design Document - 股票智能分析系统

## 1. 系统架构设计

### 1.1 整体架构

系统采用分层架构设计，从上到下分为：

```
┌────────────────────────────────────────────────────┐
│ Presentation Layer (展示层)                         │
│  - FastAPI Web UI                                  │
│  - Bot Webhook Handlers                            │
│  - GitHub Actions Workflows                        │
├────────────────────────────────────────────────────┤
│ Application Layer (应用层)                         │
│  - AnalysisService                                 │
│  - StockService                                    │
│  - TaskService                                     │
│  - NotificationService                             │
├────────────────────────────────────────────────────┤
│ Domain Layer (领域层)                               │
│  - StockAnalysisPipeline (分析流水线)              │
│  - DecisionEngine (决策引擎)                       │
│  - RiskDetector (风险检测)                         │
├────────────────────────────────────────────────────┤
│ Infrastructure Layer (基础设施层)                   │
│  - DataFetcherManager (数据源管理)                 │
│  - LLMClient (AI模型客户端)                        │
│  - NotificationSender (通知发送)                   │
│  - Repository (数据持久化)                         │
└────────────────────────────────────────────────────┘
```

### 1.2 核心模块设计

## 2. 数据层详细设计

### 2.1 数据源抽象 (BaseFetcher)

**设计模式**: Strategy Pattern (策略模式)

```python
class BaseFetcher(ABC):
    """数据源抽象基类"""

    name: str = "BaseFetcher"
    priority: int = 99  # 优先级，数字越小越优先

    @abstractmethod
    def _fetch_raw_data(self, stock_code, start_date, end_date) -> pd.DataFrame:
        """获取原始数据"""
        pass

    @abstractmethod
    def _normalize_data(self, df, stock_code) -> pd.DataFrame:
        """标准化数据"""
        pass

    def get_daily_data(self, stock_code, start_date=None, end_date=None, days=30) -> pd.DataFrame:
        """统一入口：获取日线数据"""
        # 1. 计算日期范围
        # 2. 获取原始数据
        # 3. 标准化列名
        # 4. 数据清洗
        # 5. 计算技术指标
        pass
```

**标准列名定义**:
```python
STANDARD_COLUMNS = ['date', 'open', 'high', 'low', 'close', 'volume', 'amount', 'pct_chg']
```

**技术指标计算**:
```python
def _calculate_indicators(self, df):
    # MA5, MA10, MA20: 移动平均线
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma10'] = df['close'].rolling(window=10).mean()
    df['ma20'] = df['close'].rolling(window=20).mean()

    # Volume_Ratio: 量比
    df['volume_ratio'] = df['volume'] / df['volume'].rolling(window=5).mean().shift(1)
```

### 2.2 数据源管理器 (DataFetcherManager)

**职责**:
1. 管理多个数据源实例（按优先级排序）
2. 实现自动故障切换（Failover）
3. 提供统一的数据获取接口

**故障切换策略**:
```
1. 从最高优先级数据源开始尝试
2. 捕获异常后自动切换到下一个
3. 记录每个数据源的失败原因
4. 所有数据源失败后抛出详细异常
```

**默认数据源优先级**:
```
0. EfinanceFetcher (P0) - 最高优先级
1. AkshareFetcher (P1)
2. PytdxFetcher (P2) - 通达信
3. TushareFetcher (P2) - 如配置Token则为P0
4. BaostockFetcher (P3)
5. YfinanceFetcher (P4) - 美股
```

### 2.3 数据源实现

| 数据源 | 文件 | 用途 | 特殊配置 |
|--------|------|------|----------|
| EfinanceFetcher | efinance_fetcher.py | A股日线/实时 | 无 |
| AkshareFetcher | akshare_fetcher.py | A股/港股/指数 | 无 |
| TushareFetcher | tushare_fetcher.py | A股专业数据 | 需要Token |
| PytdxFetcher | pytdx_fetcher.py | 通达信行情 | 需要通达信 |
| BaostockFetcher | baostock_fetcher.py | 融资融券数据 | 无 |
| YfinanceFetcher | yfinance_fetcher.py | 美股/港股 | 无 |

### 2.4 防封禁策略

```python
@staticmethod
def random_sleep(min_seconds=1.0, max_seconds=3.0):
    """智能随机休眠 - 模拟人类行为"""
    sleep_time = random.uniform(min_seconds, max_seconds)
    time.sleep(sleep_time)
```

**策略要点**:
1. 每个Fetcher内置流控逻辑
2. 失败自动切换到下一个数据源
3. 指数退避重试机制
4. 熔断器保护（防止连续失败）

---

## 3. 分析层详细设计

### 3.1 分析流水线 (StockAnalysisPipeline)

**核心流程**:
```
1. 初始化 (query_id生成、日志配置)
2. 获取股票数据 (调用DataFetcherManager)
3. 获取实时行情 (调用DataFetcherManager)
4. 获取筹码分布 (调用DataFetcherManager)
5. 技术面分析 (TechnicalAnalyzer)
6. 舆情分析 (NewsIntel)
7. 风险检测 (RiskDetector)
8. 决策生成 (DecisionEngine)
9. 格式化报告 (ReportFormatter)
10. 发送通知 (NotificationService)
```

### 3.2 技术分析器 (TechnicalAnalyzer)

**分析维度**:

| 维度 | 指标 | 判定逻辑 |
|------|------|----------|
| 趋势 | 多头排列 | MA5 > MA10 > MA20 |
| 趋势 | 空头排列 | MA5 < MA10 < MA20 |
| 强度 | 乖离率 | (close - MA5) / MA5 * 100 |
| 量能 | 量比 | volume / avg_volume_5 |
| 形态 | 缩量回调 | volume < MA5 && price < yesterday |
| 形态 | 放量突破 | volume > 1.5 * MA5 && close > yesterday_high |

**风险检测规则**:
```python
def check_deviation_risk(price, ma5):
    deviation = (price - ma5) / ma5 * 100
    if deviation > 5:
        return "WARNING: 乖离率超过5%，严禁追高"
    return None
```

### 3.3 决策引擎 (DecisionEngine)

**决策矩阵**:

| 条件 | 决策 |
|------|------|
| 多头排列 && 乖离率<5% && 量比>0.8 | 🟢 买入 |
| 多头排列 && 乖离率>5% | 🟡 观望（等回调） |
| 空头排列 | 🔴 卖出/观望 |
| 连续下跌 && 缩量 | 🟡 观望（可能反弹） |

**狙击点位计算**:
```python
def calculate_sniper_points(price, ma5, ma10, ma20):
    return {
        "ideal_buy": ma5,           # 最佳买点：MA5支撑
        "secondary_buy": ma10,      # 次佳买点：MA10支撑
        "stop_loss": price * 0.97, # 止损：跌3%
        "take_profit": price * 1.10 # 目标：涨10%
    }
```

### 3.4 AI模型层 (LLMClient)

**支持的模型**:
- Gemini (默认，免费)
- OpenAI 兼容 (DeepSeek、通义千问等)
- Claude
- Ollama (本地)

**请求结构**:
```python
{
    "model": "gemini-2.0-flash",
    "messages": [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_PROMPT}
    ],
    "temperature": 0.7,
    "max_tokens": 4096
}
```

---

## 4. API层详细设计

### 4.1 FastAPI应用结构

```
api/
├── app.py                 # FastAPI应用入口
├── deps.py               # 依赖注入
├── middlewares/
│   └── error_handler.py   # 错误处理中间件
└── v1/
    ├── router.py          # 路由注册
    ├── endpoints/
    │   ├── health.py      # 健康检查
    │   ├── stocks.py      # 股票接口
    │   ├── analysis.py   # 分析接口
    │   └── history.py     # 历史记录
    └── schemas/
        ├── common.py      # 通用模型
        ├── stocks.py      # 股票模型
        ├── analysis.py    # 分析模型
        └── history.py     # 历史模型
```

### 4.2 核心API端点

| 端点 | 方法 | 描述 |
|------|------|------|
| `/api/v1/health` | GET | 健康检查 |
| `/api/v1/stocks` | GET | 获取股票列表 |
| `/api/v1/stocks/{code}` | GET | 获取股票详情 |
| `/api/v1/analysis/{code}` | POST | 执行股票分析 |
| `/api/v1/analysis/batch` | POST | 批量分析 |
| `/api/v1/history` | GET | 获取分析历史 |

### 4.3 数据模型 (Pydantic)

```python
class StockInfo(BaseModel):
    code: str
    name: str
    market: str  # A股/港股/美股
    current_price: Optional[float]
    change_pct: Optional[float]

class AnalysisRequest(BaseModel):
    stock_code: str
    report_type: str = "simple"  # simple/detailed
    force_refresh: bool = False

class AnalysisResponse(BaseModel):
    stock_code: str
    stock_name: str
    report: dict
    query_id: str
```

---

## 5. 机器人层详细设计

### 5.1 平台适配器模式

**设计模式**: Adapter Pattern (适配器模式)

```
bot/
├── models.py              # 统一消息模型
├── handler.py            # Webhook处理器
├── dispatcher.py         # 命令分发器
├── platforms/
│   ├── base.py          # 平台基类 (BotPlatform)
│   ├── feishu.py        # 飞书适配器
│   ├── dingtalk.py      # 钉钉适配器
│   └── ...
└── commands/
    ├── base.py          # 命令基类
    ├── analyze.py      # 分析命令
    ├── help.py         # 帮助命令
    └── ...
```

### 5.2 统一消息模型

```python
class BotMessage(BaseModel):
    """统一消息格式"""
    platform: str        # 平台名称
    user_id: str         # 用户ID
    content: str         # 消息内容
    raw_data: dict       # 原始数据

class BotResponse(BaseModel):
    """统一响应格式"""
    text: str            # 文本内容
    markdown: str        # Markdown内容
    images: List[str]     # 图片URL列表

class WebhookResponse(BaseModel):
    """Webhook响应"""
    success: bool
    message: str
    status_code: int = 200
```

### 5.3 平台基类接口

```python
class BotPlatform(ABC):
    @property
    @abstractmethod
    def platform_name(self) -> str:
        """平台标识"""
        pass

    @abstractmethod
    def verify_request(self, headers, body) -> bool:
        """验证请求签名"""
        pass

    @abstractmethod
    def parse_message(self, data) -> Optional[BotMessage]:
        """解析消息"""
        pass

    @abstractmethod
    def format_response(self, response, message) -> WebhookResponse:
        """格式化响应"""
        pass
```

---

## 6. 通知层详细设计

### 6.1 通知服务架构

```
notification/
├── sender.py           # 发送器基类
├── wechat.py           # 企业微信
├── feishu.py           # 飞书
├── telegram.py        # Telegram
├── email.py            # 邮件
└── manager.py          # 通知管理器
```

### 6.2 通知格式

**企业微信Markdown**:
```markdown
# 📊 2026-01-10 决策仪表盘
3只股票 | 🟢买入:1 🟡观望:2 🔴卖出:0

🟢 买入 | 贵州茅台(600519)
📌 缩量回踩MA5支撑，乖离率1.2%处于最佳买点
💰 狙击: 买入1800 | 止损1750 | 目标1900
✅多头排列 ✅乖离安全 ✅量能配合
```

**飞书卡片消息**:
```json
{
    "msg_type": "interactive",
    "card": {
        "header": {
            "title": {"tag": "plain_text", "content": "决策仪表盘"},
            "template": "green"
        },
        "elements": [...]
    }
}
```

---

## 7. 数据库设计

### 7.1 数据表结构

**analysis_history (分析历史)**:
```sql
CREATE TABLE analysis_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_id VARCHAR(32) UNIQUE,
    stock_code VARCHAR(20) NOT NULL,
    stock_name VARCHAR(100),
    report_type VARCHAR(20),
    analysis_summary TEXT,
    operation_advice VARCHAR(20),
    sentiment_score INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**stock_cache (股票缓存)**:
```sql
CREATE TABLE stock_cache (
    stock_code VARCHAR(20) PRIMARY KEY,
    stock_name VARCHAR(100),
    last_price FLOAT,
    last_updated TIMESTAMP
);
```

---

## 8. 部署设计

### 8.1 Docker Compose

```yaml
version: '3.8'
services:
  analyzer:
    build: ./docker
    command: python main.py
    env_file: .env
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs

  server:
    build: ./docker
    command: python main.py --webui
    ports:
      - "8000:8000"
    env_file: .env
```

### 8.2 GitHub Actions

```yaml
name: Daily Analysis
on:
  schedule:
    - cron: '0 10 * * 1-5'  # 每天18:00北京时间
  workflow_dispatch:
jobs:
  analyze:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run Analysis
        run: python main.py
        env:
          STOCK_LIST: ${{ secrets.STOCK_LIST }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
          # ... other secrets
```

---

## 9. 配置设计

### 9.1 环境变量分类

| 分类 | 变量 | 描述 |
|------|------|------|
| AI模型 | GEMINI_API_KEY | Google AI Studio Key |
| AI模型 | OPENAI_API_KEY | OpenAI兼容API Key |
| AI模型 | OPENAI_BASE_URL | API地址 |
| 数据源 | TUSHARE_TOKEN | Tushare Pro Token |
| 通知 | WECHAT_WEBHOOK_URL | 企业微信Webhook |
| 通知 | FEISHU_WEBHOOK_URL | 飞书Webhook |
| 通知 | TELEGRAM_BOT_TOKEN | TG Bot Token |
| 配置 | STOCK_LIST | 自选股列表 |
| 配置 | REPORT_TYPE | 报告类型 |

### 9.2 配置优先级

1. 环境变量 (最高优先级)
2. .env文件
3. 默认值 (最低优先级)

---

## 10. 错误处理设计

### 10.1 异常层级

```python
class DataFetchError(Exception):
    """数据获取异常基类"""
    pass

class RateLimitError(DataFetchError):
    """API速率限制异常"""
    pass

class DataSourceUnavailableError(DataFetchError):
    """数据源不可用异常"""
    pass

class AnalysisError(Exception):
    """分析异常基类"""
    pass

class NotificationError(Exception):
    """通知发送异常"""
    pass
```

### 10.2 全局错误处理

```python
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"}
    )
```

---

## 11. 日志设计

### 11.1 日志级别

| 级别 | 使用场景 |
|------|----------|
| DEBUG | 详细调试信息 |
| INFO | 正常业务流程 |
| WARNING | 可恢复的异常 |
| ERROR | 需要关注的错误 |
| CRITICAL | 系统级错误 |

### 11.2 日志格式

```python
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
```

**输出示例**:
```
2026-01-10 14:30:15 - data_provider.base - INFO - [EfinanceFetcher] 获取 600519 数据: 2026-01-01 ~ 2026-01-10
2026-01-10 14:30:16 - data_provider.base - INFO - [EfinanceFetcher] 600519 获取成功，共 30 条数据
2026-01-10 14:30:20 - src.core.pipeline - INFO - [query_abc123] 分析完成: 600519
```

---

## 12. 监控与告警

### 12.1 健康检查

| 检查项 | 端点 | 预期响应 |
|--------|------|----------|
| API健康 | /api/v1/health | {"status": "ok"} |
| 数据库连接 | 内置 | - |
| 磁盘空间 | 内置 | - |

### 12.2 告警条件

- 数据源全部失败
- AI模型调用失败
- 通知发送失败
- 磁盘空间不足

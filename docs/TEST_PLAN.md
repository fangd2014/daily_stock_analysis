# Test Plan - 股票智能分析系统测试方案

## 1. 测试概述

### 1.1 测试目标

本文档定义了股票智能分析系统的完整测试方案，包括测试策略、测试范围、测试用例设计和执行计划。

### 1.2 测试范围

| 模块 | 测试类型 | 优先级 |
|------|----------|--------|
| 数据层 (data_provider) | 单元测试、集成测试 | P0 |
| 分析层 (src/core) | 单元测试、集成测试 | P0 |
| API层 (api) | 单元测试、API测试 | P1 |
| 机器人层 (bot) | 单元测试、集成测试 | P1 |
| 通知层 (notification) | 单元测试 | P2 |
| 端到端 | E2E测试 | P1 |

### 1.3 测试环境

| 环境 | 用途 |
|------|------|
| 开发环境 (local) | 本地开发调试 |
| 测试环境 (CI) | GitHub Actions自动化测试 |
| 生产环境 | 实际运行 |

---

## 2. 测试策略

### 2.1 测试金字塔

```
           /\
          /E2E\
         /----\
        /Integration\
       /------\
      /  Unit   \
     /----------\
```

- **单元测试 (70%)**: 覆盖各模块的核心逻辑
- **集成测试 (20%)**: 验证模块间协作
- **E2E测试 (10%)**: 验证完整业务流程

### 2.2 测试框架

| 类型 | 框架 | 用途 |
|------|------|------|
| 单元/集成 | pytest | Python单元测试 |
| Mock | unittest.mock | 模拟外部依赖 |
| 覆盖率 | pytest-cov | 代码覆盖率 |
| API测试 | requests | HTTP接口测试 |
| E2E | playwright | 浏览器自动化 |

### 2.3 测试数据策略

```python
# 测试数据使用策略
TEST_STOCK_CODES = {
    "A股": "600519",      # 贵州茅台
    "港股": "hk00700",   # 腾讯控股
    "美股": "AAPL",       # 苹果公司
}

TEST_DATE_RANGE = {
    "start": "2025-01-01",
    "end": "2025-12-31",
}
```

---

## 3. 数据层测试用例

### 3.1 BaseFetcher 单元测试

| 用例ID | 用例名称 | 预期结果 | 优先级 |
|--------|----------|----------|--------|
| DATA-001 | test_calculate_indicators_ma | MA5/MA10/MA20计算正确 | P0 |
| DATA-002 | test_calculate_indicators_volume_ratio | 量比计算正确 | P0 |
| DATA-003 | test_clean_data_remove_nulls | 空值行被正确清除 | P0 |
| DATA-004 | test_clean_data_sort_by_date | 数据按日期升序排列 | P0 |
| DATA-005 | test_random_sleep_duration | 休眠时间在指定范围内 | P1 |

**测试代码示例**:
```python
def test_calculate_indicators_ma():
    """测试MA计算"""
    from data_provider.base import BaseFetcher

    # 构造测试数据
    df = pd.DataFrame({
        'date': pd.date_range('2025-01-01', periods=10),
        'open': [100] * 10,
        'high': [105] * 10,
        'low': [95] * 10,
        'close': [100, 101, 102, 103, 104, 105, 104, 103, 102, 101],
        'volume': [1000000] * 10,
        'amount': [100000000] * 10,
        'pct_chg': [1.0] * 10,
    })

    fetcher = TestableFetcher()
    result = fetcher._calculate_indicators(df)

    # 验证MA5
    assert result['ma5'].iloc[-1] == 103.0  # (101+102+103+104+105)/5
    # 验证MA10
    assert result['ma10'].iloc[-1] == 102.5
```

### 3.2 DataFetcherManager 集成测试

| 用例ID | 用例名称 | 预期结果 | 优先级 |
|--------|----------|----------|--------|
| DATA-MGR-001 | test_priority_order | 数据源按优先级排序 | P0 |
| DATA-MGR-002 | test_failover_primary_success | 主数据源成功时直接返回 | P0 |
| DATA-MGR-003 | test_failover_secondary_success | 主失败时切换到备用 | P0 |
| DATA-MGR-004 | test_failover_all_failed | 所有数据源失败抛出异常 | P0 |
| DATA-MGR-005 | test_batch_get_stock_names | 批量获取股票名称 | P1 |

### 3.3 数据源实现测试

| 用例ID | 数据源 | 测试内容 | 优先级 |
|--------|--------|----------|--------|
| DATA-EFIN-001 | EfinanceFetcher | 获取A股日线数据 | P0 |
| DATA-AKSH-001 | AkshareFetcher | 获取指数行情 | P1 |
| DATA-YFIN-001 | YfinanceFetcher | 获取美股数据 | P1 |

---

## 4. 分析层测试用例

### 4.1 TechnicalAnalyzer 测试

| 用例ID | 用例名称 | 测试场景 | 预期结果 | 优先级 |
|--------|----------|----------|----------|--------|
| TECH-001 | test_bullish_alignment | MA5>MA10>MA20 | 返回多头排列 | P0 |
| TECH-002 | test_bearish_alignment | MA5<MA10<MA20 | 返回空头排列 | P0 |
| TECH-003 | test_deviation_high | 乖离率>5% | 返回风险警告 | P0 |
| TECH-004 | test_deviation_normal | 乖离率<3% | 返回安全 | P0 |
| TECH-005 | test_volume_ratio_normal | 量比>0.8 | 返回量能正常 | P1 |
| TECH-006 | test_volume_ratio_low | 量比<0.5 | 返回缩量 | P1 |

### 4.2 RiskDetector 测试

| 用例ID | 用例名称 | 测试场景 | 预期结果 | 优先级 |
|--------|----------|----------|----------|--------|
| RISK-001 | test_deviation_warning | price=105, ma5=100 | 返回追高警告 | P0 |
| RISK-002 | test_no_risk | price=101, ma5=100 | 返回无风险 | P0 |
| RISK-003 | test_volume_surge | volume=3*avg | 返回放量风险 | P1 |

### 4.3 DecisionEngine 测试

| 用例ID | 用例名称 | 条件组合 | 预期决策 | 优先级 |
|--------|----------|----------|----------|--------|
| DEC-001 | test_buy_signal | 多头+乖离<5%+量比>0.8 | 买入 | P0 |
| DEC-002 | test_watch_deviation | 多头+乖离>5% | 观望 | P0 |
| DEC-003 | test_sell_signal | 空头排列 | 卖出 | P0 |
| DEC-004 | test_watch_rebound | 缩量+下跌 | 观望 | P1 |

### 4.4 Pipeline 集成测试

| 用例ID | 用例名称 | 覆盖范围 | 优先级 |
|--------|----------|----------|--------|
| PIP-001 | test_pipeline_single_stock | 完整单股分析流程 | P0 |
| PIP-002 | test_pipeline_batch | 批量分析流程 | P1 |
| PIP-003 | test_pipeline_with_cache | 使用缓存的分析 | P1 |

---

## 5. API层测试用例

### 5.1 健康检查接口

| 用例ID | 接口 | 测试场景 | 预期响应 | 优先级 |
|--------|------|----------|----------|--------|
| API-HEALTH-001 | GET /api/v1/health | 正常请求 | 200, {status:ok} | P0 |
| API-HEALTH-002 | GET /api/v1/health | 服务异常 | 503, {status:error} | P1 |

### 5.2 股票接口

| 用例ID | 接口 | 测试场景 | 预期响应 | 优先级 |
|--------|------|----------|----------|--------|
| API-STOCK-001 | GET /api/v1/stocks | 正常请求 | 200, 股票列表 | P0 |
| API-STOCK-002 | GET /api/v1/stocks/600519 | 股票存在 | 200, 股票详情 | P0 |
| API-STOCK-003 | GET /api/v1/stocks/999999 | 股票不存在 | 404 | P0 |

### 5.3 分析接口

| 用例ID | 接口 | 测试场景 | 预期响应 | 优先级 |
|--------|------|----------|----------|--------|
| API-ANAL-001 | POST /api/v1/analysis/600519 | 正常分析 | 200, 分析结果 | P0 |
| API-ANAL-002 | POST /api/v1/analysis/600519 | 强制刷新 | 200, 新分析结果 | P1 |
| API-ANAL-003 | POST /api/v1/analysis/999999 | 股票不存在 | 404 | P0 |
| API-ANAL-004 | POST /api/v1/analysis/batch | 批量分析 | 200, 多只股票结果 | P1 |

### 5.4 历史记录接口

| 用例ID | 接口 | 测试场景 | 预期响应 | 优先级 |
|--------|------|----------|----------|--------|
| API-HIST-001 | GET /api/v1/history | 正常查询 | 200, 历史列表 | P1 |
| API-HIST-002 | GET /api/v1/history?limit=5 | 分页 | 200, 5条记录 | P1 |

---

## 6. 机器人层测试用例

### 6.1 消息解析测试

| 用例ID | 模块 | 测试场景 | 预期结果 | 优先级 |
|--------|------|----------|----------|--------|
| BOT-FEI-001 | 飞书 | 解析文本消息 | 正确转换为BotMessage | P0 |
| BOT-FEI-002 | 飞书 | 解析图片消息 | 正确提取图片URL | P1 |
| BOT-DING-001 | 钉钉 | 解析文本消息 | 正确转换为BotMessage | P0 |
| BOT-TG-001 | Telegram | 解析命令消息 | 正确识别命令 | P0 |

### 6.2 命令处理测试

| 用例ID | 命令 | 测试场景 | 预期结果 | 优先级 |
|--------|------|----------|----------|--------|
| BOT-CMD-001 | /analyze 600519 | 分析命令 | 返回分析结果 | P0 |
| BOT-CMD-002 | /help | 帮助命令 | 返回帮助信息 | P0 |
| BOT-CMD-003 | /status | 状态命令 | 返回系统状态 | P1 |
| BOT-CMD-004 | /batch | 批量分析 | 触发批量分析 | P1 |

### 6.3 签名验证测试

| 用例ID | 模块 | 测试场景 | 预期结果 | 优先级 |
|--------|------|----------|----------|--------|
| BOT-SIG-001 | 飞书 | 有效签名 | 验证通过 | P0 |
| BOT-SIG-002 | 飞书 | 无效签名 | 验证失败 | P0 |
| BOT-SIG-003 | 钉钉 | 有效签名 | 验证通过 | P0 |

---

## 7. 通知层测试用例

### 7.1 消息发送测试

| 用例ID | 渠道 | 测试场景 | 预期结果 | 优先级 |
|--------|------|----------|----------|--------|
| NOTIFY-001 | 企业微信 | 发送Markdown | 发送成功 | P0 |
| NOTIFY-002 | 飞书 | 发送卡片 | 发送成功 | P0 |
| NOTIFY-003 | Telegram | 发送消息 | 发送成功 | P0 |
| NOTIFY-004 | 邮件 | 发送邮件 | 发送成功 | P1 |
| NOTIFY-005 | All | 发送失败 | 记录错误日志 | P1 |

---

## 8. E2E测试用例

### 8.1 完整流程测试

| 用例ID | 场景 | 测试步骤 | 预期结果 | 优先级 |
|--------|------|----------|----------|--------|
| E2E-001 | 本地分析流程 | 1.配置环境变量<br>2.执行main.py<br>3.检查输出 | 生成分析报告 | P0 |
| E2E-002 | API触发分析 | 1.启动API服务<br>2.POST /analysis<br>3.检查返回 | 返回分析结果 | P1 |
| E2E-003 | Web界面分析 | 1.打开Web UI<br>2.点击分析按钮<br>3.查看结果 | 显示分析报告 | P1 |
| E2E-004 | 机器人触发 | 1.发送/analyze命令<br>2.等待处理<br>3.收到推送 | 收到分析结果 | P1 |

### 8.2 故障切换测试

| 用例ID | 场景 | 测试步骤 | 预期结果 | 优先级 |
|--------|------|----------|----------|--------|
| E2E-FAIL-001 | 主数据源失败 | 1.模拟主数据源故障<br>2.触发分析<br>3.检查结果 | 使用备用数据源 | P0 |

---

## 9. 测试执行计划

### 9.1 测试阶段

| 阶段 | 时间 | 执行内容 | 负责人 |
|------|------|----------|--------|
| 单元测试 | 持续 | 本地开发时自动运行 | 开发者 |
| 集成测试 | 每日 | CI定时执行 | CI系统 |
| E2E测试 | 发布前 | 手动执行 | QA |
| 回归测试 | 每次发版 | 完整测试套件 | CI系统 |

### 9.2 CI/CD集成

```yaml
# .github/workflows/test.yml
name: Test
on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.10'
      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install pytest pytest-cov
      - name: Run unit tests
        run: pytest tests/ --cov=src --cov=data_provider
      - name: Run integration tests
        run: pytest tests/integration/
```

### 9.3 测试覆盖率目标

| 模块 | 覆盖率目标 |
|------|------------|
| data_provider | > 80% |
| src/core | > 75% |
| src/services | > 70% |
| api | > 80% |
| bot | > 70% |

---

## 10. 测试数据管理

### 10.1 测试数据 fixtures

```python
# tests/fixtures/__init__.py
import pytest
import pandas as pd
from datetime import datetime, timedelta

@pytest.fixture
def sample_stock_data():
    """样本股票数据"""
    dates = pd.date_range(end=datetime.now(), periods=30, freq='D')
    return pd.DataFrame({
        'date': dates,
        'open': [100 + i for i in range(30)],
        'high': [105 + i for i in range(30)],
        'low': [95 + i for i in range(30)],
        'close': [100 + i for i in range(30)],
        'volume': [1000000] * 30,
        'amount': [100000000] * 30,
        'pct_chg': [1.0] * 30,
    })

@pytest.fixture
def mock_env_vars(monkeypatch):
    """模拟环境变量"""
    monkeypatch.setenv('STOCK_LIST', '600519,hk00700,AAPL')
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
```

---

## 11. 缺陷管理

### 11.1 缺陷等级定义

| 等级 | 定义 | 处理时限 |
|------|------|----------|
| P0-Critical | 系统崩溃、数据丢失 | 24小时 |
| P1-High | 核心功能不可用 | 3天 |
| P2-Medium | 功能异常但有替代方案 | 1周 |
| P3-Low | 界面/体验问题 | 2周 |

### 11.2 缺陷跟踪

| 字段 | 说明 |
|------|------|
| ID | 缺陷唯一标识 |
| Title | 缺陷简述 |
| Severity | 严重等级 |
| Module | 所属模块 |
| Steps | 重现步骤 |
| Expected | 预期行为 |
| Actual | 实际行为 |
| Status | 状态(Open/In Progress/Resolved/Closed) |

---

## 12. 附录

### 12.1 测试命令

```bash
# 运行所有测试
pytest

# 运行单元测试
pytest tests/ -m "not integration"

# 运行集成测试
pytest tests/integration/ -m integration

# 运行特定模块测试
pytest tests/test_data_provider.py -v

# 生成覆盖率报告
pytest --cov=src --cov-report=html

# 运行E2E测试
playwright test tests/e2e/
```

### 12.2 测试环境配置

```bash
# 测试环境变量
export TEST_MODE=true
export TEST_DATA_PATH=./tests/fixtures/
export MOCK_EXTERNAL_API=true
```

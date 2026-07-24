# -*- coding: utf-8 -*-
"""
===================================
Test Fixtures and Configuration
===================================

Shared test fixtures for the test suite.
"""

import sys
import os
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta

import pandas as pd
import numpy as np


# Test stock codes
TEST_STOCK_CODES = {
    "A股": "600519",       # 贵州茅台
    "港股": "hk00700",    # 腾讯控股
    "美股": "AAPL",        # 苹果公司
    "A股2": "000001",     # 平安银行
    "美股2": "TSLA",      # 特斯拉
}

TEST_DATE_RANGE = {
    "start": "2025-01-01",
    "end": "2025-12-31",
}


def create_sample_stock_data(days=30, start_price=100.0):
    """
    Create sample stock data for testing.

    Args:
        days: Number of days
        start_price: Starting price

    Returns:
        DataFrame with OHLCV data
    """
    dates = pd.date_range(end=datetime.now(), periods=days, freq='D')
    close_prices = [start_price + i * 0.5 for i in range(days)]

    return pd.DataFrame({
        'date': dates,
        'open': [p + 0.2 for p in close_prices],
        'high': [p + 2.0 for p in close_prices],
        'low': [p - 2.0 for p in close_prices],
        'close': close_prices,
        'volume': [1000000 + i * 10000 for i in range(days)],
        'amount': [100000000 + i * 1000000 for i in range(days)],
        'pct_chg': [0.5 + i * 0.1 for i in range(days)],
    })


def create_bullish_stock_data():
    """
    Create stock data with bullish pattern (MA5 > MA10 > MA20).

    Returns:
        DataFrame with bullish indicators
    """
    days = 30
    dates = pd.date_range(end=datetime.now(), periods=days, freq='D')

    # Create declining MA values (MA5 > MA10 > MA20 for bullish)
    base = 100.0
    ma5 = [base + i * 0.5 for i in range(days)]
    ma10 = [base - 2 + i * 0.5 for i in range(days)]
    ma20 = [base - 5 + i * 0.5 for i in range(days)]

    return pd.DataFrame({
        'date': dates,
        'open': ma5,
        'high': [m + 2 for m in ma5],
        'low': [m - 2 for m in ma5],
        'close': ma5,
        'volume': [1000000] * days,
        'amount': [100000000] * days,
        'pct_chg': [0.5] * days,
        'ma5': ma5,
        'ma10': ma10,
        'ma20': ma20,
    })


def create_bearish_stock_data():
    """
    Create stock data with bearish pattern (MA5 < MA10 < MA20).

    Returns:
        DataFrame with bearish indicators
    """
    days = 30
    dates = pd.date_range(end=datetime.now(), periods=days, freq='D')

    # Create declining MA values (MA5 < MA10 < MA20 for bearish)
    base = 100.0
    ma5 = [base - i * 0.5 for i in range(days)]
    ma10 = [base + 2 - i * 0.5 for i in range(days)]
    ma20 = [base + 5 - i * 0.5 for i in range(days)]

    return pd.DataFrame({
        'date': dates,
        'open': ma5,
        'high': [m + 2 for m in ma5],
        'low': [m - 2 for m in ma5],
        'close': ma5,
        'volume': [1000000] * days,
        'amount': [100000000] * days,
        'pct_chg': [-0.5] * days,
        'ma5': ma5,
        'ma10': ma10,
        'ma20': ma20,
    })


def create_high_deviation_data():
    """
    Create data with high deviation (>5%).

    Returns:
        DataFrame with high price deviation
    """
    days = 10
    dates = pd.date_range(end=datetime.now(), periods=days, freq='D')

    # MA5 at 100, but price at 106 (6% deviation)
    ma5 = [100.0] * days
    close_prices = [106.0] * days  # 6% above MA5

    return pd.DataFrame({
        'date': dates,
        'open': close_prices,
        'high': [p + 1 for p in close_prices],
        'low': [p - 1 for p in close_prices],
        'close': close_prices,
        'volume': [1000000] * days,
        'amount': [100000000] * days,
        'pct_chg': [6.0] * days,
        'ma5': ma5,
    })


def create_low_volume_data():
    """
    Create data with low volume ratio (<0.5).

    Returns:
        DataFrame with low volume
    """
    days = 10
    dates = pd.date_range(end=datetime.now(), periods=days, freq='D')

    # Previous 5 days at 2M, today at 0.8M (ratio = 0.4)
    volumes = [2000000, 2000000, 2000000, 2000000, 2000000, 800000, 800000, 800000, 800000, 800000]

    return pd.DataFrame({
        'date': dates,
        'open': [100] * days,
        'high': [105] * days,
        'low': [95] * days,
        'close': [100] * days,
        'volume': volumes,
        'amount': [100000000] * days,
        'pct_chg': [0] * days,
    })


class MockAnalysisResult:
    """Mock AnalysisResult for testing"""

    def __init__(self, code="600519", name="贵州茅台"):
        self.code = code
        self.name = name
        self.current_price = 1800.0
        self.change_pct = 1.25
        self.analysis_summary = "测试分析摘要"
        self.operation_advice = "买入"
        self.trend_prediction = "上涨趋势"
        self.sentiment_score = 75
        self.news_summary = "相关新闻摘要"
        self.technical_analysis = "技术面分析"
        self.fundamental_analysis = "基本面分析"
        self.risk_warning = None

    def get_sniper_points(self):
        return {
            "ideal_buy": 1800,
            "secondary_buy": 1785,
            "stop_loss": 1750,
            "take_profit": 1900,
        }


class MockConfig:
    """Mock Config for testing"""

    def __init__(self):
        self.stock_list = "600519,hk00700,AAPL"
        self.gemini_api_key = "test_key"
        self.openai_api_key = None
        self.report_type = "simple"
        self.enable_realtime_quote = True
        self.enable_chip_distribution = True
        self.realtime_source_priority = "efinance,akshare_em"
        self.tushare_token = None


def patch_config():
    """Decorator to patch config in tests"""
    return patch('src.config.get_config', return_value=MockConfig())


def patch_data_fetcher():
    """Decorator to patch data fetcher"""
    mock_fetcher = Mock()
    mock_fetcher.get_daily_data.return_value = create_sample_stock_data()
    mock_fetcher.get_realtime_quote.return_value = Mock(
        current=1800.0,
        change_pct=1.25,
        name="贵州茅台"
    )
    return mock_fetcher


# Pytest fixtures (if using pytest)
try:
    import pytest

    @pytest.fixture
    def sample_data():
        """Sample stock data fixture"""
        return create_sample_stock_data()

    @pytest.fixture
    def bullish_data():
        """Bullish pattern data fixture"""
        return create_bullish_stock_data()

    @pytest.fixture
    def bearish_data():
        """Bearish pattern data fixture"""
        return create_bearish_stock_data()

    @pytest.fixture
    def mock_config():
        """Mock config fixture"""
        return MockConfig()

    @pytest.fixture
    def mock_analysis_result():
        """Mock analysis result fixture"""
        return MockAnalysisResult()

except ImportError:
    pass


if __name__ == "__main__":
    # Run basic tests
    print("Testing fixtures...")

    data = create_sample_stock_data()
    print(f"Created sample data: {len(data)} rows")

    bullish = create_bullish_stock_data()
    print(f"Created bullish data: MA5={bullish['ma5'].iloc[-1]}, MA10={bullish['ma10'].iloc[-1]}, MA20={bullish['ma20'].iloc[-1]}")

    print("All fixtures working!")

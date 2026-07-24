import type React from 'react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { quantApi } from '../api/quant';
import type { QuantLatestResult, QuantRun, QuantSelection, QuantStrategy } from '../types/quant';
import './QuantPage.css';

const STATUS_LABEL: Record<string, string> = {
  queued: '等待执行',
  running: '执行中',
  completed: '执行完成',
  failed: '执行失败',
};

const RUN_IDLE_MESSAGE = (
  '点击“手动启动策略”后，这里会实时显示数据加载、条件过滤、排序和报告生成日志。'
);

const formatDateTime = (value?: string): string => {
  if (!value) return '--';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(parsed);
};

const formatNumber = (value?: number, digits = 2): string => {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits }).format(value);
};

const formatPercent = (value?: number, digits = 1): string => {
  if (value === null || value === undefined || Number.isNaN(value)) return '--';
  return `${(value * 100).toFixed(digits)}%`;
};

const StrategyIcon: React.FC<{ strategyId: string }> = ({ strategyId }) => {
  if (strategyId === 'etf-momentum') {
    return <span aria-hidden="true">↗</span>;
  }
  if (strategyId === 'four-factor') {
    return <span aria-hidden="true">✦</span>;
  }
  if (strategyId === 'hot-sector-commanders') {
    return <span aria-hidden="true">⌁</span>;
  }
  return <span aria-hidden="true">⌘</span>;
};

const MetricCard: React.FC<{ label: string; value: string; tone?: string }> = ({ label, value, tone = '' }) => (
  <div className={`quant-metric ${tone}`}>
    <span>{label}</span>
    <strong>{value}</strong>
  </div>
);

const RuleList: React.FC<{ title: string; items: string[]; tone: 'cyan' | 'green' | 'amber' }> = ({
  title,
  items,
  tone,
}) => (
  <section className={`quant-rule-block ${tone}`}>
    <h3>{title}</h3>
    <ol>
      {items.map((item, index) => (
        <li key={`${title}-${index}`}>{item}</li>
      ))}
    </ol>
  </section>
);

const StrategyParameters: React.FC<{ parameters: Record<string, unknown> }> = ({ parameters }) => (
  <details className="quant-parameters">
    <summary>查看完整策略参数</summary>
    <pre>{JSON.stringify(parameters, null, 2)}</pre>
  </details>
);

const RunConsole: React.FC<{ run?: QuantRun; idleMessage: string }> = ({ run, idleMessage }) => {
  const consoleRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (consoleRef.current) {
      consoleRef.current.scrollTop = consoleRef.current.scrollHeight;
    }
  }, [run?.logs.length]);

  return (
    <section className="quant-panel quant-console-panel" aria-live="polite">
      <div className="quant-panel-heading">
        <div>
          <span className="quant-kicker">EXECUTION TRACE</span>
          <h2>策略执行过程</h2>
        </div>
        {run ? (
          <span className={`quant-status ${run.status}`}>{STATUS_LABEL[run.status]}</span>
        ) : (
          <span className="quant-status idle">尚未启动</span>
        )}
      </div>
      {run && (run.status === 'queued' || run.status === 'running') && (
        <div className="quant-progress" aria-label={`执行进度 ${run.progress}%`}>
          <span style={{ width: `${run.progress}%` }} />
        </div>
      )}
      <div className="quant-console" ref={consoleRef} role="log" aria-label="策略执行日志">
        {run?.logs.length ? (
          run.logs.map((entry) => (
            <div className={`quant-log ${entry.level.toLowerCase()}`} key={entry.sequence}>
              <time>{formatDateTime(entry.timestamp)}</time>
              <span>{entry.level}</span>
              <p>{entry.message}</p>
            </div>
          ))
        ) : (
          <div className="quant-console-empty">
            <span aria-hidden="true">$</span>
            <p>{idleMessage}</p>
          </div>
        )}
      </div>
      {run?.error && <div className="quant-run-error">{run.error}</div>}
    </section>
  );
};

const selectionScore = (strategyId: string, selection: QuantSelection): string => {
  if (selection.score === null || selection.score === undefined) return '--';
  if (strategyId === 'chip-double-peak') return formatPercent(selection.score, 2);
  return formatNumber(selection.score, 3);
};

const ResultsTable: React.FC<{ strategy: QuantStrategy; result?: QuantLatestResult }> = ({ strategy, result }) => (
  <section className="quant-panel quant-results-panel">
    <div className="quant-panel-heading">
      <div>
        <span className="quant-kicker">LATEST SELECTION</span>
        <h2>选股结果</h2>
      </div>
      <div className="quant-result-cutoff">
        <span>信号日期</span>
        <strong>{result?.asOf || '暂无报告'}</strong>
      </div>
    </div>
    {result?.selections.length ? (
      <div className="quant-table-wrap">
        <table className="quant-table">
          <thead>
            <tr>
              <th>排名 / 标的</th>
              <th>板块 / 类别</th>
              <th>参考价格</th>
              <th>{strategy.strategyId === 'chip-double-peak' ? '距低峰' : '策略分'}</th>
              <th>目标仓位</th>
              <th>买入上限 / 参考位</th>
              <th>止损参考</th>
              <th>入选理由</th>
            </tr>
          </thead>
          <tbody>
            {result.selections.map((selection, index) => (
              <tr key={`${selection.symbol}-${index}`}>
                <td>
                  <div className="quant-symbol-cell">
                    <span>{String(index + 1).padStart(2, '0')}</span>
                    <div>
                      <strong>{selection.name || selection.symbol}</strong>
                      <small>{selection.symbol}</small>
                    </div>
                  </div>
                </td>
                <td>{selection.group || '--'}</td>
                <td className="number">{formatNumber(selection.price, 3)}</td>
                <td className="number accent">{selectionScore(strategy.strategyId, selection)}</td>
                <td className="number">{formatPercent(selection.targetWeight)}</td>
                <td className="number">{formatNumber(selection.buyReference, 3)}</td>
                <td className="number danger">{formatNumber(selection.stopReference, 3)}</td>
                <td className="reason">{selection.reason || '--'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ) : (
      <div className="quant-empty-state">
        <span aria-hidden="true">∅</span>
        <div>
          <strong>{result ? '本期没有标的通过全部门槛' : '尚未生成策略报告'}</strong>
          <p>
            {result
              ? '空名单是风控结果，不使用条件不足的股票补位。'
              : '启动策略后，结果会自动刷新到这里。'}
          </p>
        </div>
      </div>
    )}
  </section>
);

const OperationGuide: React.FC<{ result?: QuantLatestResult }> = ({ result }) => (
  <section className="quant-panel quant-guide-panel">
    <div className="quant-panel-heading">
      <div>
        <span className="quant-kicker">PAPER TRADING PLAYBOOK</span>
        <h2>操作指引</h2>
      </div>
      <span className="quant-paper-badge">仅模拟盘</span>
    </div>
    <div className="quant-guide-grid">
      {(result?.guidance || ['等待策略输出后生成操作指引。']).map((item, index) => (
        <article key={`${item}-${index}`}>
          <span>{String(index + 1).padStart(2, '0')}</span>
          <p>{item}</p>
        </article>
      ))}
    </div>
    {result?.selections.some((item) => item.operationGuide) && (
      <details className="quant-stock-guides">
        <summary>查看逐股操作说明</summary>
        <div>
          {result.selections
            .filter((item) => item.operationGuide)
            .map((item) => (
              <article key={item.symbol}>
                <strong>{item.name} · {item.symbol}</strong>
                <p>{item.operationGuide}</p>
              </article>
            ))}
        </div>
      </details>
    )}
    <p className="quant-disclaimer">
      策略输出用于研究和模拟执行，不构成投资建议，也不保证未来收益。
    </p>
  </section>
);

const QuantPage: React.FC = () => {
  const [strategies, setStrategies] = useState<QuantStrategy[]>([]);
  const [selectedId, setSelectedId] = useState<string>('');
  const [run, setRun] = useState<QuantRun>();
  const [isLoading, setIsLoading] = useState(true);
  const [isStarting, setIsStarting] = useState(false);
  const [error, setError] = useState<string>();

  const selected = useMemo(
    () => strategies.find((strategy) => strategy.strategyId === selectedId),
    [strategies, selectedId],
  );
  const displayedResult = run?.status === 'completed' && run.result ? run.result : selected?.latestResult;
  const activeRunId = run?.runId;
  const activeRunStatus = run?.status;
  const isRunActive = (
    isStarting
    || selected?.status === 'running'
    || run?.status === 'running'
    || run?.status === 'queued'
  );

  const loadStrategies = useCallback(async (preferredId?: string) => {
    try {
      const response = await quantApi.getStrategies();
      setStrategies(response.strategies);
      setSelectedId((current) => preferredId || current || response.strategies[0]?.strategyId || '');
      setError(undefined);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '量化策略加载失败');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadStrategies();
  }, [loadStrategies]);

  useEffect(() => {
    const activeRunId = selected?.activeRunId;
    if (!activeRunId) {
      setRun((current) => (current?.strategyId === selectedId ? current : undefined));
      return;
    }
    void quantApi.getRun(activeRunId).then(setRun).catch(() => undefined);
  }, [selected?.activeRunId, selectedId]);

  useEffect(() => {
    if (!activeRunId || (activeRunStatus !== 'queued' && activeRunStatus !== 'running')) return undefined;
    const timer = window.setInterval(async () => {
      try {
        const updated = await quantApi.getRun(activeRunId);
        setRun(updated);
        if (updated.status === 'completed' || updated.status === 'failed') {
          await loadStrategies(updated.strategyId);
        }
      } catch (caught) {
        setError(caught instanceof Error ? caught.message : '执行状态刷新失败');
      }
    }, 1200);
    return () => window.clearInterval(timer);
  }, [activeRunId, activeRunStatus, loadStrategies]);

  const handleStrategyChange = (strategy: QuantStrategy) => {
    setSelectedId(strategy.strategyId);
    setError(undefined);
    if (strategy.activeRunId) {
      void quantApi.getRun(strategy.activeRunId).then(setRun).catch(() => undefined);
    } else if (run?.strategyId !== strategy.strategyId) {
      setRun(undefined);
    }
  };

  const handleStart = async () => {
    if (!selected || isStarting) return;
    setIsStarting(true);
    setError(undefined);
    try {
      const created = await quantApi.startRun(selected.strategyId);
      setRun(created);
      setStrategies((current) => current.map((strategy) => (
        strategy.strategyId === selected.strategyId
          ? { ...strategy, status: 'running', activeRunId: created.runId }
          : strategy
      )));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '策略启动失败');
    } finally {
      setIsStarting(false);
    }
  };

  if (isLoading) {
    return (
      <div className="quant-loading">
        <span />
        <p>正在加载量化策略工作台…</p>
      </div>
    );
  }

  return (
    <div className="quant-page">
      <header className="quant-header">
        <div>
          <span className="quant-eyebrow">QUANT RESEARCH / PAPER EXECUTION</span>
          <h1>量化选股工作台</h1>
          <p>策略规则、运行证据、选股结果和次日操作计划集中审计。</p>
        </div>
        <div className="quant-header-status">
          <span className="live-dot" />
          <div>
            <small>系统状态</small>
            <strong>模拟研究环境</strong>
          </div>
        </div>
      </header>

      {error && (
        <div className="quant-alert" role="alert">
          <span aria-hidden="true">!</span>
          <p>{error}</p>
          <button type="button" onClick={() => setError(undefined)} aria-label="关闭错误提示">×</button>
        </div>
      )}

      <main className="quant-workspace">
        <aside className="quant-strategy-rail" aria-label="量化策略列表">
          <div className="quant-rail-heading">
            <span>策略池</span>
            <strong>{strategies.length}</strong>
          </div>
          <div className="quant-strategy-list">
            {strategies.map((strategy) => (
              <button
                type="button"
                key={strategy.strategyId}
                className={`quant-strategy-card ${selectedId === strategy.strategyId ? 'active' : ''}`}
                onClick={() => handleStrategyChange(strategy)}
                aria-pressed={selectedId === strategy.strategyId}
              >
                <span className="quant-strategy-icon"><StrategyIcon strategyId={strategy.strategyId} /></span>
                <span className="quant-strategy-copy">
                  <small>{strategy.category}</small>
                  <strong>{strategy.name}</strong>
                  <em>{strategy.latestResult?.asOf ? `信号 ${strategy.latestResult.asOf}` : '等待首次运行'}</em>
                </span>
                {strategy.status === 'running' && <span className="quant-card-running" aria-label="执行中" />}
              </button>
            ))}
          </div>
          <div className="quant-rail-note">
            <span>SAFE MODE</span>
            <p>页面只能启动已注册的模拟策略，不连接券商，也不接受任意命令。</p>
          </div>
        </aside>

        {selected ? (
          <div className="quant-content">
            <section className="quant-hero">
              <div className="quant-hero-copy">
                <div className="quant-chip-row">
                  <span>{selected.category}</span>
                  <span>{selected.schedule}</span>
                  <span>{selected.dataSource}</span>
                </div>
                <h2>{selected.name}</h2>
                <p>{selected.summary}</p>
                <div className="quant-factor-row">
                  {selected.factors.map((factor) => <span key={factor}>{factor}</span>)}
                </div>
              </div>
              <div className="quant-run-box">
                <span>MANUAL CONTROL</span>
                <button
                  type="button"
                  className="quant-run-button"
                  onClick={handleStart}
                  disabled={isRunActive}
                >
                  {isRunActive ? (
                    <><i className="spinner" />策略执行中</>
                  ) : (
                    <><i className="play-icon" />手动启动策略</>
                  )}
                </button>
                <small>配置：{selected.configPath}</small>
                <small>
                  最近运行：{formatDateTime(selected.latestRun?.completedAt || selected.latestRun?.createdAt)}
                </small>
              </div>
            </section>

            <section className="quant-metrics" aria-label="最新策略指标">
              <MetricCard label="本期入选" value={`${displayedResult?.selectedCount ?? 0} 只`} tone="cyan" />
              <MetricCard
                label="总收益率"
                value={formatPercent(displayedResult?.metrics?.totalReturn as number | undefined)}
                tone="green"
              />
              <MetricCard
                label="年化收益"
                value={formatPercent(displayedResult?.metrics?.annualizedReturn as number | undefined)}
              />
              <MetricCard
                label="最大回撤"
                value={formatPercent(displayedResult?.metrics?.maxDrawdown as number | undefined)}
                tone="red"
              />
              <MetricCard label="报告更新" value={formatDateTime(displayedResult?.updatedAt)} />
            </section>

            <div className="quant-detail-grid">
              <section className="quant-panel quant-rules-panel">
                <div className="quant-panel-heading">
                  <div>
                    <span className="quant-kicker">STRATEGY SPECIFICATION</span>
                    <h2>策略内容</h2>
                  </div>
                  <span className="quant-version">已锁定参数</span>
                </div>
                <RuleList title="买入门槛" items={selected.entryRules} tone="cyan" />
                <RuleList title="卖出纪律" items={selected.exitRules} tone="green" />
                <RuleList title="组合风控" items={selected.riskRules} tone="amber" />
                <StrategyParameters parameters={selected.parameters} />
              </section>
              <RunConsole
                run={run?.strategyId === selected.strategyId ? run : undefined}
                idleMessage={RUN_IDLE_MESSAGE}
              />
            </div>

            <ResultsTable strategy={selected} result={displayedResult} />
            <OperationGuide result={displayedResult} />
          </div>
        ) : (
          <div className="quant-empty-state"><strong>没有可用的量化策略</strong></div>
        )}
      </main>
    </div>
  );
};

export default QuantPage;

export type QuantRunStatus = 'queued' | 'running' | 'completed' | 'failed';

export interface QuantSelection {
  symbol?: string;
  name?: string;
  group?: string;
  price?: number;
  score?: number;
  targetWeight?: number;
  buyReference?: number;
  stopReference?: number;
  reason?: string;
  operationGuide?: string;
}

export interface QuantLatestResult {
  asOf?: string;
  executeDate?: string;
  updatedAt: string;
  reportPath: string;
  selectedCount: number;
  eligibleCount?: number;
  candidateCount?: number;
  selections: QuantSelection[];
  metrics: Record<string, number | string | null>;
  guidance: string[];
  hotSectors?: Array<Record<string, unknown>>;
  dataErrors?: Record<string, string>;
}

export interface QuantRunSummary {
  runId: string;
  strategyId: string;
  strategyName: string;
  status: QuantRunStatus;
  progress: number;
  createdAt: string;
  startedAt?: string;
  completedAt?: string;
  error?: string;
  exitCode?: number;
  result?: QuantLatestResult;
}

export interface QuantLogEntry {
  sequence: number;
  timestamp: string;
  level: 'INFO' | 'WARNING' | 'ERROR';
  message: string;
}

export interface QuantRun extends QuantRunSummary {
  logs: QuantLogEntry[];
}

export interface QuantStrategy {
  strategyId: string;
  name: string;
  category: string;
  summary: string;
  schedule: string;
  dataSource: string;
  configPath: string;
  status: 'ready' | 'running';
  activeRunId?: string;
  factors: string[];
  entryRules: string[];
  exitRules: string[];
  riskRules: string[];
  parameters: Record<string, unknown>;
  latestResult?: QuantLatestResult;
  latestRun?: QuantRunSummary;
}

export interface QuantStrategyListResponse {
  total: number;
  strategies: QuantStrategy[];
}

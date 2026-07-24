import apiClient from './index';
import { toCamelCase } from './utils';
import type { QuantRun, QuantStrategy, QuantStrategyListResponse } from '../types/quant';

export const quantApi = {
  getStrategies: async (): Promise<QuantStrategyListResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/quant/strategies');
    return toCamelCase<QuantStrategyListResponse>(response.data);
  },

  getStrategy: async (strategyId: string): Promise<QuantStrategy> => {
    const response = await apiClient.get<Record<string, unknown>>(`/api/v1/quant/strategies/${strategyId}`);
    return toCamelCase<QuantStrategy>(response.data);
  },

  startRun: async (strategyId: string): Promise<QuantRun> => {
    const response = await apiClient.post<Record<string, unknown>>(
      '/api/v1/quant/runs',
      { strategy_id: strategyId },
      { validateStatus: (status) => status === 202 || status === 409 },
    );
    const data = toCamelCase<QuantRun & { message?: string }>(response.data);
    if (response.status === 409) {
      const error = new Error(data.message || '该策略已有执行中的任务');
      Object.assign(error, data);
      throw error;
    }
    return data;
  },

  getRun: async (runId: string): Promise<QuantRun> => {
    const response = await apiClient.get<Record<string, unknown>>(`/api/v1/quant/runs/${runId}`);
    return toCamelCase<QuantRun>(response.data);
  },
};

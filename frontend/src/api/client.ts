// 后端接入层：对齐 P1 正式会话接口。
//   POST /v1/sessions
//   GET  /v1/sessions/{session_id}
//   POST /v1/sessions/{session_id}/answers
//   GET  /v1/capabilities

import type {
  AnswerRequest,
  CapabilityStatus,
  CreateSessionRequest,
  ErrorResponse,
  ResolvedSnapshot,
  WorkflowSnapshot,
} from '@/types';
import { mockEngine } from './mockEngine';

const USE_MOCK = import.meta.env.VITE_USE_MOCK === 'true';
const API_BASE = import.meta.env.VITE_API_BASE ?? '';

export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  readonly retryable: boolean;
  readonly reason?: string;

  constructor(status: number, payload?: Partial<ErrorResponse> | null, fallback?: string) {
    const message = payload?.message || fallback || `请求失败（${status}）`;
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = payload?.code;
    this.retryable = Boolean(payload?.retryable);
    const reason = payload?.details?.reason;
    this.reason = typeof reason === 'string' ? reason : undefined;
  }
}

export interface CasePathApi {
  capabilities(): Promise<CapabilityStatus[]>;
  analyze(query: string): Promise<ResolvedSnapshot>;
  refresh(sessionId: string): Promise<ResolvedSnapshot>;
  answer(req: { session_id: string } & AnswerRequest): Promise<ResolvedSnapshot>;
}

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));
const MOCK_LATENCY = 600;

const emptyResolved = (snapshot: WorkflowSnapshot): ResolvedSnapshot => ({
  ...snapshot,
  rules: {},
  cases: {},
  spans: {},
});

async function readError(res: Response): Promise<ApiError> {
  try {
    const payload = (await res.json()) as ErrorResponse;
    return new ApiError(res.status, payload);
  } catch {
    return new ApiError(res.status, null, `请求失败（${res.status}）`);
  }
}

async function parseSnapshot(res: Response): Promise<ResolvedSnapshot> {
  if (!res.ok) throw await readError(res);
  return emptyResolved((await res.json()) as WorkflowSnapshot);
}

const mockApi: CasePathApi = {
  async capabilities() {
    return [
      {
        contract_version: '1.1',
        capability: 'llm',
        available: false,
        mode: 'DEMO',
        degraded: true,
        reason: '前端 Mock，未调用真实模型。',
      },
    ];
  },
  async analyze(query) {
    await delay(MOCK_LATENCY);
    return mockEngine.analyze(query);
  },
  async refresh(sessionId) {
    await delay(MOCK_LATENCY);
    return mockEngine.skip(sessionId);
  },
  async answer(req) {
    await delay(MOCK_LATENCY);
    return mockEngine.answer(req.session_id, req.condition_id, req.answer);
  },
};

const httpApi: CasePathApi = {
  async capabilities() {
    const res = await fetch(`${API_BASE}/v1/capabilities`);
    if (!res.ok) throw await readError(res);
    return (await res.json()) as CapabilityStatus[];
  },
  async analyze(query) {
    const body: CreateSessionRequest = { contract_version: '1.2', query };
    const res = await fetch(`${API_BASE}/v1/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    return parseSnapshot(res);
  },
  async refresh(sessionId) {
    const res = await fetch(`${API_BASE}/v1/sessions/${sessionId}`);
    return parseSnapshot(res);
  },
  async answer(req) {
    const { session_id, ...answer } = req;
    const res = await fetch(`${API_BASE}/v1/sessions/${session_id}/answers`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(answer),
    });
    return parseSnapshot(res);
  },
};

export const api: CasePathApi = USE_MOCK ? mockApi : httpApi;

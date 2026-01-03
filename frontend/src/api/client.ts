import type {
  AccountSummaryRow,
  ApiHealth,
  ConfigEffectiveResponse,
  EventRow,
  LiveStatus,
  OpenOrderRow,
  OpenOrderSnapshotRow,
  OrderReviewRow,
  PerformanceRow,
  PerformanceSummary,
  PromptTemplates,
  PositionReviewRow,
  PositionRow,
  RedditPostRow,
  RedditSentimentRow,
  RedditState,
  ResearchRow,
  RuntimeConfig,
  TradeRow,
} from './types'

function toErrorMessage(err: unknown): string {
  if (err instanceof Error) return err.message
  if (typeof err === 'string') return err
  return 'Unknown error'
}

async function fetchJson<T>(path: string, timeoutMs = 8000, init?: RequestInit): Promise<T> {
  const controller = new AbortController()
  const id = setTimeout(() => controller.abort(), timeoutMs)

  try {
    const initHeaders = (init?.headers || {}) as Record<string, string>
    const headers: Record<string, string> = { Accept: 'application/json', ...initHeaders }
    if (init?.body !== undefined && headers['Content-Type'] === undefined) {
      headers['Content-Type'] = 'application/json'
    }

    const res = await fetch(path, {
      ...init,
      headers,
      signal: controller.signal,
    })
    clearTimeout(id)

    if (!res.ok) {
      const text = await res.text().catch(() => '')
      throw new Error(`${res.status} ${res.statusText}${text ? ` — ${text}` : ''}`)
    }
    return (await res.json()) as T
  } catch (err) {
    clearTimeout(id)
    throw err
  }
}

export const api = {
  toErrorMessage,
  health: () => fetchJson<ApiHealth>('/api/health'),
  liveStatus: () => fetchJson<LiveStatus>('/api/live-status'),
  configEffective: () => fetchJson<ConfigEffectiveResponse>('/api/config/effective'),
  promptTemplates: () => fetchJson<PromptTemplates>('/api/config/prompt-templates'),
  runtimeConfigGet: () => fetchJson<RuntimeConfig>('/api/config/runtime'),
  runtimeConfigPut: (cfg: RuntimeConfig) =>
    fetchJson<RuntimeConfig>('/api/config/runtime', 8000, {
      method: 'PUT',
      body: JSON.stringify(cfg),
    }),
  // History (DB-backed)
  events: (limit = 200) => fetchJson<EventRow[]>(`/api/history/events?limit=${limit}`),
  research: (limit = 200) => fetchJson<ResearchRow[]>(`/api/history/research?limit=${limit}`),
  trades: (limit = 500) => fetchJson<TradeRow[]>(`/api/history/trades?limit=${limit}`),
  performance: () => fetchJson<PerformanceRow[]>('/api/history/performance'),
  performanceSummary: () => fetchJson<PerformanceSummary>('/api/history/performance/summary'),
  positionReviews: (limit = 100) => fetchJson<PositionReviewRow[]>(`/api/history/position-reviews?limit=${limit}`),
  orderReviews: (limit = 100) => fetchJson<OrderReviewRow[]>(`/api/history/order-reviews?limit=${limit}`),

  // Live (IBKR-backed)
  accountSummaryLatest: () => fetchJson<AccountSummaryRow[]>('/api/live/account-summary'),
  positionsLatest: () => fetchJson<PositionRow[]>('/api/live/positions'),
  openOrdersLatest: () => fetchJson<OpenOrderRow[]>('/api/live/open-orders'),

  // Trader snapshot (DB-backed) — shows orders as seen by the trading client/session.
  openOrdersSnapshotLatest: (limit = 2000) =>
    fetchJson<OpenOrderSnapshotRow[]>(`/api/history/open-orders?limit=${limit}`),

  redditState: () => fetchJson<RedditState>('/api/reddit/state'),
  redditPosts: (limit = 300) => fetchJson<RedditPostRow[]>(`/api/reddit/posts?limit=${limit}`),
  redditSentimentLatest: () => fetchJson<RedditSentimentRow[]>('/api/reddit/sentiment/latest'),
  subscribeEvents: (
    afterId: number,
    onEvent: (ev: EventRow) => void,
    onError: (err: unknown) => void,
  ): EventSource => {
    const es = new EventSource(`/api/events/stream?after_id=${afterId}`)
    es.onmessage = (msg) => {
      try {
        onEvent(JSON.parse(msg.data) as EventRow)
      } catch (e) {
        onError(e)
      }
    }
    es.onerror = (e) => onError(e)
    return es
  },
}



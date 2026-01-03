export type ApiHealth = {
  status: string
  db_path: string
}

export type LiveStatus = {
  id?: number
  current_symbol: string | null
  current_step: string | null
  last_update: string | null
}

export type RuntimeStrategy = {
  name: string
  overrides: Record<string, any>
  description?: string
}

export type RuntimeConfig = {
  schema_version: number
  overrides: Record<string, any>
  strategies: RuntimeStrategy[]
  active_strategy: string | null
}

export type ConfigEffectiveResponse = {
  runtime: RuntimeConfig
  effective: Record<string, any>
}

export type PromptTemplates = {
  shortlist: string
  buy_selection: string
  position_review: string
  order_review: string
}

export type EventRow = {
  id: number
  timestamp: string
  level: string | null
  symbol: string | null
  step: string | null
  message: string | null
}

export type AccountSummaryRow = {
  id: number
  timestamp: string
  tag: string
  value: number
  currency: string
}

export type PositionRow = {
  id: number
  timestamp: string
  account: string | null
  symbol: string | null
  exchange: string | null
  currency: string | null
  position: number | null
  avg_cost: number | null
  market_price: number | null
  market_value: number | null
  unrealised_pnl: number | null
  realised_pnl: number | null
}

export type OpenOrderRow = {
  order_id: number | null
  symbol: string | null
  exchange: string | null
  currency: string | null
  action: string | null
  order_type: string | null
  total_qty: number | null
  filled: number | null
  remaining: number | null
  status: string | null
  lmt_price: number | null
  aux_price: number | null
}

export type OpenOrderSnapshotRow = {
  id: number
  timestamp: string
  order_id: number | null
  symbol: string | null
  exchange: string | null
  currency: string | null
  action: string | null
  order_type: string | null
  total_qty: number | null
  filled: number | null
  remaining: number | null
  status: string | null
  lmt_price: number | null
  aux_price: number | null
}

export type TradeRow = {
  id: number
  timestamp: string
  symbol: string | null
  action: string | null
  quantity: number | null
  price: number | null
  stop_loss: number | null
  take_profit: number | null
  sentiment_score: number | null
  status: string | null
  rationale: string | null
}

export type ResearchRow = {
  id: number
  timestamp: string
  symbol: string | null
  exchange: string | null
  currency: string | null
  price: number | null
  rsi: number | null
  volatility_ratio: number | null
  sentiment_score: number | null
  ai_reasoning: string | null
  score: number | null
  rank: number | null
  reddit_mentions: number | null
  reddit_sentiment: number | null
  reddit_confidence: number | null
  reddit_override: number | null
  decision: string | null
  reason: string | null
}

export type PerformanceRow = {
  id: number
  timestamp: string
  equity: number | null
  unrealized_pnl: number | null
  realized_pnl: number | null
}

export type PerformanceSummary = {
  baseline_timestamp?: string
  baseline_equity?: number
  latest_timestamp?: string
  latest_equity?: number
  delta_equity?: number
  delta_pct?: number | null
}

export type RedditState = {
  last_fetch_utc: number
  last_analysis_utc: number
}

export type RedditPostRow = {
  id: number
  fetched_at: string
  reddit_id: string
  subreddit: string
  created_utc: number
  title: string | null
  selftext: string | null
  permalink: string | null
  ups: number | null
  num_comments: number | null
}

export type RedditSentimentRow = {
  id: number
  timestamp: string
  symbol: string | null
  mentions: number | null
  sentiment: number | null
  confidence: number | null
  rationale: string | null
  source_fetch_utc: number | null
}

export type PositionReviewRow = {
  id: number
  timestamp: string
  symbol: string | null
  exchange: string | null
  currency: string | null
  entry_price: number | null
  current_price: number | null
  quantity: number | null
  unrealised_pnl: number | null
  pnl_pct: number | null
  minutes_held: number | null
  current_stop_loss: number | null
  current_take_profit: number | null
  action: string | null
  new_stop_loss: number | null
  new_take_profit: number | null
  confidence: number | null
  urgency: number | null
  rationale: string | null
  key_factors: string | null
  executed: number | null
}

export type OrderReviewRow = {
  id: number
  timestamp: string
  order_id: number | null
  symbol: string | null
  order_type: string | null
  order_action: string | null
  order_quantity: number | null
  order_price: number | null
  current_price: number | null
  bid_price: number | null
  ask_price: number | null
  price_distance_pct: number | null
  order_age_minutes: number | null
  action: string | null
  new_price: number | null
  confidence: number | null
  rationale: string | null
  executed: number | null
}


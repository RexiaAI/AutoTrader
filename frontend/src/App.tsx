import './App.css'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { api } from './api/client'
import type {
  AccountSummaryRow,
  ConfigEffectiveResponse,
  EventRow,
  LiveStatus,
  OpenOrderRow,
  OpenOrderSnapshotRow,
  OrderReviewRow,
  PerformanceSummary,
  PromptTemplates,
  PositionReviewRow,
  PositionRow,
  RedditSentimentRow,
  RedditState,
  ResearchRow,
  RuntimeConfig,
  RuntimeStrategy,
  TradeRow,
} from './api/types'
import { Kpi } from './components/Kpi'
import { Modal } from './components/Modal'
import { Panel } from './components/Panel'
import { Pill } from './components/Pill'
import { Table, type Column } from './components/Table'
import { formatDateTime, formatInteger, formatMoney, formatNumber } from './utils/format'

// Number of consecutive failures before showing as disconnected
const DISCONNECT_THRESHOLD = 2
const IBKR_DISCONNECT_THRESHOLD = 2

/** Info icon with hover tooltip */
function InfoIcon({ tip }: { tip: string }) {
  return (
    <span className="info-icon">
      i<span className="info-icon__tip">{tip}</span>
    </span>
  )
}

function App() {
  const [apiConnected, setApiConnected] = useState<boolean>(false)
  const [apiError, setApiError] = useState<string | null>(null)
  const [_dbPath, setDbPath] = useState<string | null>(null) // Reserved for future use
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)
  const failureCountRef = useRef<number>(0)
  const ibkrFailureCountRef = useRef<number>(0)
  const [ibkrStatus, setIbkrStatus] = useState<'connected' | 'disconnected' | 'unknown'>('unknown')

  const [live, setLive] = useState<LiveStatus | null>(null)
  const [accountSummary, setAccountSummary] = useState<AccountSummaryRow[]>([])
  const [positions, setPositions] = useState<PositionRow[]>([])
  const [openOrders, setOpenOrders] = useState<OpenOrderRow[]>([])
  const [openOrdersSnapshot, setOpenOrdersSnapshot] = useState<OpenOrderSnapshotRow[]>([])
  const [research, setResearch] = useState<ResearchRow[]>([])
  const [trades, setTrades] = useState<TradeRow[]>([])
  const [redditState, setRedditState] = useState<RedditState | null>(null)
  const [redditSentiment, setRedditSentiment] = useState<RedditSentimentRow[]>([])

  const [events, setEvents] = useState<EventRow[]>([])
  const [eventsError, setEventsError] = useState<string | null>(null)

  const seenEventIds = useRef<Set<number>>(new Set())

  const [researchQuery, setResearchQuery] = useState<string>('')
  const [researchDecision, setResearchDecision] = useState<string>('ALL')
  const [researchCompact, setResearchCompact] = useState<boolean>(true)
  const [researchLatestPerSymbol, setResearchLatestPerSymbol] = useState<boolean>(true)
  const [selectedResearch, setSelectedResearch] = useState<ResearchRow | null>(null)

  const [eventQuery, setEventQuery] = useState<string>('')
  const [eventLevel, setEventLevel] = useState<string>('ALL')
  const [selectedEvent, setSelectedEvent] = useState<EventRow | null>(null)

  const [selectedReddit, setSelectedReddit] = useState<RedditSentimentRow | null>(null)
  
  const [positionReviews, setPositionReviews] = useState<PositionReviewRow[]>([])
  const [selectedReview, setSelectedReview] = useState<PositionReviewRow | null>(null)
  
  const [orderReviews, setOrderReviews] = useState<OrderReviewRow[]>([])
  const [selectedOrderReview, setSelectedOrderReview] = useState<OrderReviewRow | null>(null)

  const [performanceSummary, setPerformanceSummary] = useState<PerformanceSummary | null>(null)

  const [ordersView, setOrdersView] = useState<'trader' | 'ibkr'>('trader')

  const [settingsOpen, setSettingsOpen] = useState(false)
  const [settingsLoading, setSettingsLoading] = useState(false)
  const [settingsSaving, setSettingsSaving] = useState(false)
  const [settingsError, setSettingsError] = useState<string | null>(null)
  const [settingsData, setSettingsData] = useState<ConfigEffectiveResponse | null>(null)
  const [settingsDraft, setSettingsDraft] = useState<RuntimeConfig | null>(null)
  const [promptTemplates, setPromptTemplates] = useState<PromptTemplates | null>(null)

  useEffect(() => {
    if (!settingsOpen) return
    let cancelled = false
    setSettingsLoading(true)
    setSettingsError(null)
    setSettingsData(null)
    setSettingsDraft(null)
    setPromptTemplates(null)

    void Promise.allSettled([api.configEffective(), api.promptTemplates()])
      .then(([cfgRes, tmplRes]) => {
        if (cancelled) return

        if (cfgRes.status === 'fulfilled') {
          setSettingsData(cfgRes.value)
          setSettingsDraft(cfgRes.value.runtime)
        } else {
          setSettingsError(api.toErrorMessage(cfgRes.reason))
        }

        if (tmplRes.status === 'fulfilled') {
          setPromptTemplates(tmplRes.value)
        }
      })
      .finally(() => {
        if (cancelled) return
        setSettingsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [settingsOpen])

  const [leftTab, setLeftTab] = useState<'research' | 'shortlist' | 'trades'>('research')
  const [rightTab, setRightTab] = useState<'events' | 'positions' | 'orders' | 'reddit' | 'reviews' | 'orderReviews'>('events')

  const pickTag = useCallback((rows: AccountSummaryRow[], tag: string, currency: string | null = null): AccountSummaryRow | undefined => {
    const matches = rows.filter((r) => r.tag === tag)
    if (matches.length === 0) return undefined
    if (currency) return matches.find((r) => r.currency === currency)
    return matches.find((r) => r.currency === 'GBP') ?? matches.find((r) => r.currency === 'BASE') ?? matches[0]
  }, [])

  const cashRow = useMemo(() => pickTag(accountSummary, 'TotalCashValue') ?? pickTag(accountSummary, 'AvailableFunds'), [
    accountSummary,
    pickTag,
  ])
  const cashUsdRow = useMemo(() => pickTag(accountSummary, 'TotalCashValue', 'USD') ?? pickTag(accountSummary, 'CashBalance', 'USD'), [
    accountSummary,
    pickTag,
  ])
  const netLiqRow = useMemo(() => pickTag(accountSummary, 'NetLiquidation'), [accountSummary, pickTag])
  const grossPosRow = useMemo(() => pickTag(accountSummary, 'GrossPositionValue'), [accountSummary, pickTag])

  const openPositionsCount = useMemo(
    () => positions.filter((p) => (p.position ?? 0) !== 0).length,
    [positions],
  )

  const traderPill = useMemo(() => {
    if (!live?.last_update) {
      return { tone: 'neutral' as const, label: 'Trader —', title: 'No trader updates yet' }
    }
    const ts = Date.parse(live.last_update)
    if (!Number.isFinite(ts)) {
      return { tone: 'neutral' as const, label: 'Trader —', title: 'Invalid live status timestamp' }
    }

    // Heuristic: if the trader hasn't updated live status recently, consider it inactive.
    // We use a generous threshold to cover "market closed" sleep intervals.
    const ageMinutes = (Date.now() - ts) / 60000
    const active = ageMinutes <= 90
    const title = `Last update ${formatDateTime(live.last_update)} (${Math.round(ageMinutes)} min ago)`
    return {
      tone: active ? ('good' as const) : ('warn' as const),
      label: active ? 'Trader active' : 'Trader inactive',
      title,
    }
  }, [live?.last_update])

  const ibkrPill = useMemo(() => {
    if (!apiConnected) return { tone: 'neutral' as const, label: 'IBKR —', title: 'API offline' }
    if (ibkrStatus === 'connected') return { tone: 'good' as const, label: 'IBKR connected', title: 'IBKR service is connected' }
    if (ibkrStatus === 'disconnected')
      return { tone: 'bad' as const, label: 'IBKR disconnected', title: 'IBKR service is not connected' }
    return { tone: 'neutral' as const, label: 'IBKR —', title: 'IBKR status unknown' }
  }, [apiConnected, ibkrStatus])

  // Calculate total UPNL and RPNL from live IBKR data - prefer USD currency
  const totalUpnl = useMemo(() => {
    // Prefer USD, then BASE, then fallback to summing positions
    const usdUpnl = accountSummary.find((r) => r.tag === 'UnrealizedPnL' && r.currency === 'USD')
    if (usdUpnl && usdUpnl.value !== null) return usdUpnl.value
    const baseUpnl = accountSummary.find((r) => r.tag === 'UnrealizedPnL' && r.currency === 'BASE')
    if (baseUpnl && baseUpnl.value !== null) return baseUpnl.value
    return positions.reduce((sum, p) => sum + (p.unrealised_pnl ?? 0), 0)
  }, [accountSummary, positions])
  
  const accountPnlDelta = useMemo(() => {
    const d = performanceSummary?.delta_equity
    if (typeof d !== 'number') return null
    return d
  }, [performanceSummary])

  const accountPnlDeltaPct = useMemo(() => {
    const p = performanceSummary?.delta_pct
    if (typeof p !== 'number') return null
    return p * 100
  }, [performanceSummary])

  const normaliseMarkets = (ms: unknown): string[] => {
    if (!Array.isArray(ms)) return []
    const out: string[] = []
    for (const m of ms) {
      if (typeof m !== 'string') continue
      const mm = m.trim().toUpperCase()
      if (mm === 'US' || mm === 'UK') out.push(mm)
    }
    // de-dupe whilst preserving order
    return Array.from(new Set(out))
  }

  const listToLines = (v: unknown): string => {
    if (!Array.isArray(v)) return ''
    return v
      .map((x) => (typeof x === 'string' ? x.trim() : String(x)))
      .filter((x) => Boolean(x))
      .join('\n')
  }

  const linesToList = (s: string): string[] =>
    String(s || '')
      .split('\n')
      .map((x) => x.trim())
      .filter((x) => Boolean(x))

  const getDraftOverride = (path: Array<string>): any => {
    let cur: any = settingsDraft?.overrides ?? null
    for (const k of path) {
      if (!cur || typeof cur !== 'object') return undefined
      cur = cur[k]
    }
    return cur
  }

  const setDraftOverride = (path: Array<string>, value: any) => {
    if (!settingsDraft) return
    const next: RuntimeConfig = JSON.parse(JSON.stringify(settingsDraft))
    let cur: any = next.overrides
    for (let i = 0; i < path.length - 1; i++) {
      const k = path[i]
      if (!cur[k] || typeof cur[k] !== 'object') cur[k] = {}
      cur = cur[k]
    }
    cur[path[path.length - 1]] = value
    setSettingsDraft(next)
  }

  const deleteDraftOverride = (path: Array<string>) => {
    if (!settingsDraft) return
    const next: RuntimeConfig = JSON.parse(JSON.stringify(settingsDraft))
    let cur: any = next.overrides
    for (let i = 0; i < path.length - 1; i++) {
      const k = path[i]
      if (!cur || typeof cur !== 'object') return
      cur = cur[k]
    }
    if (!cur || typeof cur !== 'object') return
    delete cur[path[path.length - 1]]
    setSettingsDraft(next)
  }

  const updateStrategy = (name: string, updater: (s: RuntimeStrategy) => RuntimeStrategy) => {
    if (!settingsDraft) return
    const next: RuntimeConfig = JSON.parse(JSON.stringify(settingsDraft))
    next.strategies = next.strategies.map((s) => (s.name === name ? updater(s) : s))
    setSettingsDraft(next)
  }

  const activeStrategyName = (settingsDraft?.active_strategy || settingsData?.runtime.active_strategy || 'Default') as string
  const activeStrategy = (settingsDraft?.strategies || []).find((s) => s.name === activeStrategyName) || null

  const getActiveStrategyOverride = (path: Array<string>) => {
    let cur: any = activeStrategy?.overrides ?? null
    for (const k of path) {
      if (!cur || typeof cur !== 'object') return undefined
      cur = cur[k]
    }
    return cur
  }

  const setActiveStrategyOverride = (path: Array<string>, value: any) => {
    if (!settingsDraft) return
    if (!activeStrategy) return
    updateStrategy(activeStrategy.name, (s) => {
      const nextS: RuntimeStrategy = JSON.parse(JSON.stringify(s))
      let cur: any = nextS.overrides
      for (let i = 0; i < path.length - 1; i++) {
        const k = path[i]
        if (!cur[k] || typeof cur[k] !== 'object') cur[k] = {}
        cur = cur[k]
      }
      cur[path[path.length - 1]] = value
      return nextS
    })
  }

  const deleteActiveStrategyOverride = (path: Array<string>) => {
    if (!settingsDraft) return
    if (!activeStrategy) return
    updateStrategy(activeStrategy.name, (s) => {
      const nextS: RuntimeStrategy = JSON.parse(JSON.stringify(s))
      let cur: any = nextS.overrides
      for (let i = 0; i < path.length - 1; i++) {
        const k = path[i]
        if (!cur || typeof cur !== 'object') return nextS
        cur = cur[k]
      }
      if (!cur || typeof cur !== 'object') return nextS
      delete cur[path[path.length - 1]]
      return nextS
    })
  }

  const refresh = useCallback(async () => {
    try {
      const health = await api.health()
      // Success - reset failure counter and mark as connected
      failureCountRef.current = 0
      setApiConnected(true)
      setApiError(null)
      setDbPath(health.db_path)
    } catch (e) {
      // Increment failure counter
      failureCountRef.current += 1
      // Only mark as disconnected after DISCONNECT_THRESHOLD consecutive failures
      if (failureCountRef.current >= DISCONNECT_THRESHOLD) {
        setApiConnected(false)
        setApiError(api.toErrorMessage(e))
        setIbkrStatus('unknown')
      }
      return
    }

    const settled = await Promise.allSettled([
      api.liveStatus(),
      api.accountSummaryLatest(),
      api.positionsLatest(),
      api.openOrdersLatest(),
      api.openOrdersSnapshotLatest(),
      api.research(200),
      api.trades(200),
      api.redditState(),
      api.redditSentimentLatest(),
      api.positionReviews(100),
      api.orderReviews(100),
      api.performanceSummary(),
    ])

    const errors: string[] = []
    const apply = <T,>(idx: number, label: string, setter: (v: T) => void) => {
      const r = settled[idx]
      if (r.status === 'fulfilled') {
        setter(r.value as T)
      } else {
        errors.push(`${label}: ${api.toErrorMessage(r.reason)}`)
      }
    }

    apply<LiveStatus>(0, 'Live status', setLive)
    apply<AccountSummaryRow[]>(1, 'Account summary', setAccountSummary)
    apply<PositionRow[]>(2, 'Positions', setPositions)
    apply<OpenOrderRow[]>(3, 'Open orders (IBKR)', setOpenOrders)
    apply<OpenOrderSnapshotRow[]>(4, 'Open orders (trader snapshot)', setOpenOrdersSnapshot)
    apply<ResearchRow[]>(5, 'Research', setResearch)
    apply<TradeRow[]>(6, 'Trades', setTrades)
    apply<RedditState>(7, 'Reddit state', setRedditState)
    apply<RedditSentimentRow[]>(8, 'Reddit sentiment', setRedditSentiment)
    apply<PositionReviewRow[]>(9, 'Position reviews', setPositionReviews)
    apply<OrderReviewRow[]>(10, 'Order reviews', setOrderReviews)
    apply<PerformanceSummary>(11, 'Performance summary', setPerformanceSummary)

    // IBKR connectivity: if all live IBKR-backed endpoints fail, mark disconnected.
    const ibkrOk = settled[1].status === 'fulfilled' || settled[2].status === 'fulfilled' || settled[3].status === 'fulfilled'
    if (ibkrOk) {
      ibkrFailureCountRef.current = 0
      setIbkrStatus('connected')
    } else {
      ibkrFailureCountRef.current += 1
      if (ibkrFailureCountRef.current >= IBKR_DISCONNECT_THRESHOLD) {
        setIbkrStatus('disconnected')
      }
    }

    if (settled.some((r) => r.status === 'fulfilled')) {
      setLastRefresh(new Date())
    }

    if (errors.length === 0) {
      setApiError(null)
    } else {
      const head = errors.slice(0, 2).join(' | ')
      const tail = errors.length > 2 ? ` (+${errors.length - 2} more)` : ''
      setApiError(`${head}${tail}`)
    }
  }, [])

  useEffect(() => {
    const t = window.setTimeout(() => {
      void refresh()
    }, 0)
    const id = window.setInterval(() => {
      void refresh()
    }, 5000)
    return () => {
      window.clearTimeout(t)
      window.clearInterval(id)
    }
  }, [refresh])

  useEffect(() => {
    let es: EventSource | null = null
    let cancelled = false

    const start = async () => {
      try {
        const initial = await api.events(200)
        if (cancelled) return
        setEvents(initial)
        seenEventIds.current = new Set(initial.map((e) => e.id))

        const afterId = initial[0]?.id ?? 0
        es = api.subscribeEvents(
          afterId,
          (ev) => {
            if (seenEventIds.current.has(ev.id)) return
            seenEventIds.current.add(ev.id)
            setEvents((prev) => [ev, ...prev].slice(0, 500))
          },
          (err) => setEventsError(api.toErrorMessage(err)),
        )
        // Clear the badge once the stream is (re)connected.
        es.onopen = () => setEventsError(null)
        setEventsError(null)
      } catch (e) {
        if (cancelled) return
        setEventsError(api.toErrorMessage(e))
      }
    }

    start()
    return () => {
      cancelled = true
      es?.close()
    }
  }, [])

  const levelTone = (level: string | null | undefined) => {
    const l = (level || '').toUpperCase()
    if (l.includes('ERROR') || l.includes('CRIT')) return 'bad'
    if (l.includes('WARN')) return 'warn'
    if (l.includes('INFO')) return 'good'
    return 'neutral'
  }

  const decisionTone = (decision: string | null | undefined) => {
    const d = (decision || '').toUpperCase()
    if (d.includes('BUY') || d.includes('EXEC') || d.includes('TRADE')) return 'good'
    if (d.includes('SHORTLIST') || d.includes('DEFER') || d.includes('ELIGIBLE')) return 'warn'
    if (d.includes('REJECT') || d.includes('SKIP')) return 'neutral'
    if (d.includes('FAIL') || d.includes('ERROR')) return 'bad'
    if (!d) return 'neutral'
    return 'neutral'
  }

  const decisionLabel = (decision: string | null | undefined) => {
    const d = (decision || '').toUpperCase()
    if (!d) return '—'
    if (d.includes('SHORTLIST') || d.includes('DEFER') || d.includes('ELIGIBLE')) return 'SHORTLISTED'
    if (d.includes('BUY') || d.includes('EXEC') || d.includes('TRADE')) return 'BOUGHT'
    if (d.includes('REJECT')) return 'REJECTED'
    if (d.includes('SKIP')) return 'SKIPPED'
    return d
  }

  const renderKValue = (val: string | null | undefined) => {
    if (!val) return '—'
    const trimmed = val.trim()
    if (trimmed.startsWith('[') && trimmed.endsWith(']')) {
      try {
        const parsed = JSON.parse(trimmed)
        if (Array.isArray(parsed)) {
  return (
            <ul className="modal-list">
              {parsed.map((item, i) => (
                <li key={i}>{item}</li>
              ))}
            </ul>
          )
        }
      } catch (e) {
        // Fall back to raw text if parse fails
      }
    }
    return val
  }

  const eventsColumns: Array<Column<EventRow>> = [
    {
      header: 'Time',
      width: '135px',
      render: (r) => <span className="mono">{formatDateTime(r.timestamp)}</span>,
    },
    {
      header: 'Lvl',
      width: '65px',
      render: (r) => <Pill label={(r.level || '—').toUpperCase()} tone={levelTone(r.level)} />,
      align: 'centre',
    },
    {
      header: 'Sym',
      width: '65px',
      render: (r) => <span className="mono">{r.symbol || '—'}</span>,
    },
    {
      header: 'Step',
      width: '110px',
      render: (r) => <span className="mono">{r.step || '—'}</span>,
    },
    { header: 'Message', width: 'auto', render: (r) => <span>{r.message || '—'}</span> },
  ]

  const researchColumnsCompact: Array<Column<ResearchRow>> = [
    { header: 'Time', width: '135px', render: (r) => <span className="mono">{formatDateTime(r.timestamp)}</span> },
    { header: 'Sym', width: '65px', render: (r) => <span className="mono">{r.symbol || '—'}</span> },
    { header: 'Price', width: '85px', align: 'right', render: (r) => formatMoney(r.price, r.currency) },
    { header: 'RSI', width: '50px', align: 'right', render: (r) => formatNumber(r.rsi, 1) },
    { header: 'Vol', width: '50px', align: 'right', render: (r) => formatNumber(r.volatility_ratio, 2) },
    { header: 'AI', width: '50px', align: 'right', render: (r) => formatNumber(r.sentiment_score, 2) },
    { header: 'Score', width: '55px', align: 'right', render: (r) => formatNumber(r.score, 2) },
    {
      header: 'Decision',
      width: '90px',
      align: 'centre',
      render: (r) => <Pill label={decisionLabel(r.decision)} tone={decisionTone(r.decision)} />,
    },
    { header: 'Reason', width: 'auto', render: (r) => <span>{r.reason || '—'}</span> },
  ]

  const researchColumnsDetailed: Array<Column<ResearchRow>> = [
    ...researchColumnsCompact.slice(0, 2),
    { header: 'Exch', width: '60px', render: (r) => <span className="mono">{r.exchange || '—'}</span> },
    { header: 'Ccy', width: '45px', render: (r) => <span className="mono">{r.currency || '—'}</span> },
    ...researchColumnsCompact.slice(2, 7),
    { header: 'Rank', width: '45px', align: 'right', render: (r) => formatInteger(r.rank) },
    { header: 'R mtns', width: '60px', align: 'right', render: (r) => formatInteger(r.reddit_mentions) },
    { header: 'R sent', width: '55px', align: 'right', render: (r) => formatNumber(r.reddit_sentiment, 2) },
    {
      header: 'Ovr',
      width: '50px',
      align: 'centre',
      render: (r) => (r.reddit_override ? <Pill label="YES" tone="warn" /> : <span className="muted">—</span>),
    },
    ...researchColumnsCompact.slice(7),
  ]

  const positionsColumns: Array<Column<PositionRow>> = [
    { header: 'Sym', width: '65px', render: (r) => <span className="mono">{r.symbol || '—'}</span> },
    { header: 'Exch', width: '60px', render: (r) => <span className="mono">{r.exchange || '—'}</span> },
    { header: 'Ccy', width: '45px', render: (r) => <span className="mono">{r.currency || '—'}</span> },
    { header: 'Pos', width: '60px', align: 'right', render: (r) => formatNumber(r.position, 0) },
    { header: 'Avg', width: '80px', align: 'right', render: (r) => formatMoney(r.avg_cost, r.currency) },
    { header: 'Mkt', width: '80px', align: 'right', render: (r) => formatMoney(r.market_price, r.currency) },
    { header: 'Value', width: '90px', align: 'right', render: (r) => formatMoney(r.market_value, r.currency) },
    {
      header: 'UPnL',
      width: '95px',
      align: 'right',
      render: (r) => {
        const v = r.unrealised_pnl
        const cls = v !== null && v !== undefined && v < 0 ? 'badText' : 'goodText'
        return <span className={v === null || v === undefined ? 'muted' : cls}>{formatMoney(v, r.currency)}</span>
      },
    },
  ]

  const ordersColumns: Array<Column<OpenOrderRow>> = [
    { header: 'ID', width: '50px', align: 'right', render: (r) => formatInteger(r.order_id) },
    { header: 'Sym', width: '65px', render: (r) => <span className="mono">{r.symbol || '—'}</span> },
    { header: 'Act', width: '50px', render: (r) => <span className="mono">{r.action || '—'}</span> },
    { header: 'Type', width: '55px', render: (r) => <span className="mono">{r.order_type || '—'}</span> },
    { header: 'Qty', width: '60px', align: 'right', render: (r) => formatNumber(r.total_qty, 0) },
    { header: 'Fill', width: '60px', align: 'right', render: (r) => formatNumber(r.filled, 0) },
    { header: 'Rem', width: '60px', align: 'right', render: (r) => formatNumber(r.remaining, 0) },
    { header: 'Status', width: '80px', render: (r) => <span className="mono">{r.status || '—'}</span> },
    { header: 'Limit', width: '80px', align: 'right', render: (r) => formatMoney(r.lmt_price, r.currency) },
    { header: 'Aux', width: '80px', align: 'right', render: (r) => formatMoney(r.aux_price, r.currency) },
  ]

  const ordersSnapshotColumns: Array<Column<OpenOrderSnapshotRow>> = [
    { header: 'Time', width: '135px', render: (r) => <span className="mono">{formatDateTime(r.timestamp)}</span> },
    { header: 'ID', width: '50px', align: 'right', render: (r) => formatInteger(r.order_id) },
    { header: 'Sym', width: '65px', render: (r) => <span className="mono">{r.symbol || '—'}</span> },
    { header: 'Act', width: '50px', render: (r) => <span className="mono">{r.action || '—'}</span> },
    { header: 'Type', width: '55px', render: (r) => <span className="mono">{r.order_type || '—'}</span> },
    { header: 'Qty', width: '60px', align: 'right', render: (r) => formatNumber(r.total_qty, 0) },
    { header: 'Fill', width: '60px', align: 'right', render: (r) => formatNumber(r.filled, 0) },
    { header: 'Rem', width: '60px', align: 'right', render: (r) => formatNumber(r.remaining, 0) },
    { header: 'Status', width: '80px', render: (r) => <span className="mono">{r.status || '—'}</span> },
    {
      header: 'Limit',
      width: '80px',
      align: 'right',
      render: (r) => formatMoney(r.lmt_price !== null && r.lmt_price !== undefined && Math.abs(r.lmt_price) > 1e100 ? null : r.lmt_price, r.currency),
    },
    {
      header: 'Aux',
      width: '80px',
      align: 'right',
      render: (r) => formatMoney(r.aux_price !== null && r.aux_price !== undefined && Math.abs(r.aux_price) > 1e100 ? null : r.aux_price, r.currency),
    },
  ]

  const tradesColumns: Array<Column<TradeRow>> = [
    { header: 'Time', width: '135px', render: (r) => <span className="mono">{formatDateTime(r.timestamp)}</span> },
    { header: 'Sym', width: '65px', render: (r) => <span className="mono">{r.symbol || '—'}</span> },
    { header: 'Act', width: '65px', render: (r) => <span className="mono">{r.action || '—'}</span> },
    { header: 'Qty', width: '60px', align: 'right', render: (r) => formatInteger(r.quantity) },
    { header: 'Price', width: '90px', align: 'right', render: (r) => formatNumber(r.price, 4) },
    { header: 'Stop', width: '90px', align: 'right', render: (r) => formatNumber(r.stop_loss, 4) },
    { header: 'TP', width: '90px', align: 'right', render: (r) => formatNumber(r.take_profit, 4) },
    { header: 'Status', width: '85px', render: (r) => <span className="mono">{r.status || '—'}</span> },
    { header: 'Rationale', width: 'auto', render: (r) => <span>{r.rationale || '—'}</span> },
  ]

  const redditColumns: Array<Column<RedditSentimentRow>> = [
    { header: 'Time', width: '135px', render: (r) => <span className="mono">{formatDateTime(r.timestamp)}</span> },
    { header: 'Sym', width: '65px', render: (r) => <span className="mono">{r.symbol || '—'}</span> },
    { header: 'Mtns', width: '65px', align: 'right', render: (r) => formatInteger(r.mentions) },
    { header: 'Sent', width: '55px', align: 'right', render: (r) => formatNumber(r.sentiment, 2) },
    { header: 'Conf', width: '55px', align: 'right', render: (r) => formatNumber(r.confidence, 2) },
    { header: 'Rationale', width: 'auto', render: (r) => <span>{r.rationale || '—'}</span> },
  ]

  const reviewColumns: Array<Column<PositionReviewRow>> = [
    { header: 'Time', width: '135px', render: (r) => <span className="mono">{formatDateTime(r.timestamp)}</span> },
    { header: 'Sym', width: '60px', render: (r) => <span className="mono">{r.symbol || '—'}</span> },
    {
      header: 'Action',
      width: '90px',
      render: (r) => {
        const action = r.action || '—'
        const exec = r.executed === 1
        const tone = action === 'SELL' ? 'bad' : action === 'HOLD' ? 'good' : action.startsWith('ADJUST') ? 'warn' : 'neutral'
        return (
          <span>
            <Pill tone={tone} label={action} />
            {exec && <span className="muted" style={{ marginLeft: 4, fontSize: '0.75rem' }}>✓</span>}
          </span>
        )
      },
    },
    {
      header: 'P&L%',
      width: '65px',
      align: 'right',
      render: (r) => (
        <span className={r.pnl_pct && r.pnl_pct >= 0 ? 'text-green' : 'text-red'}>
          {formatNumber(r.pnl_pct, 1)}%
        </span>
      ),
    },
    { header: 'Conf', width: '50px', align: 'right', render: (r) => formatNumber(r.confidence, 2) },
    { header: 'Urg', width: '50px', align: 'right', render: (r) => formatNumber(r.urgency, 2) },
    { header: 'Rationale', width: 'auto', render: (r) => <span>{r.rationale || '—'}</span> },
  ]

  const orderReviewColumns: Array<Column<OrderReviewRow>> = [
    { header: 'Time', width: '135px', render: (r) => <span className="mono">{formatDateTime(r.timestamp)}</span> },
    { header: 'Sym', width: '60px', render: (r) => <span className="mono">{r.symbol || '—'}</span> },
    { header: 'Type', width: '60px', render: (r) => <span className="mono">{r.order_action}/{r.order_type}</span> },
    {
      header: 'Action',
      width: '100px',
      render: (r) => {
        const action = r.action || '—'
        const exec = r.executed === 1
        const tone = action === 'CANCEL' ? 'bad' : action === 'ADJUST_PRICE' ? 'warn' : action === 'KEEP' ? 'good' : 'neutral'
        return (
          <span>
            <Pill tone={tone} label={action} />
            {exec && <span className="muted" style={{ marginLeft: 4, fontSize: '0.75rem' }}>✓</span>}
          </span>
        )
      },
    },
    {
      header: 'Order Price',
      width: '80px',
      align: 'right',
      render: (r) => formatMoney(r.order_price, r.symbol?.includes('.') ? 'GBP' : 'USD'),
    },
    {
      header: 'Current',
      width: '80px',
      align: 'right',
      render: (r) => formatMoney(r.current_price, r.symbol?.includes('.') ? 'GBP' : 'USD'),
    },
    {
      header: 'Dist %',
      width: '65px',
      align: 'right',
      render: (r) => (
        <span className={r.price_distance_pct && Math.abs(r.price_distance_pct) > 3 ? 'text-red' : ''}>
          {formatNumber(r.price_distance_pct, 1)}%
        </span>
      ),
    },
    { header: 'Age', width: '50px', align: 'right', render: (r) => <span>{r.order_age_minutes ?? '—'}m</span> },
    { header: 'Conf', width: '50px', align: 'right', render: (r) => formatNumber(r.confidence, 2) },
    { header: 'Rationale', width: 'auto', render: (r) => <span>{r.rationale || '—'}</span> },
  ]

  const filteredResearch = useMemo(() => {
    const q = researchQuery.trim().toUpperCase()
    const base = research.filter((r) => {
      const sym = (r.symbol || '').toUpperCase()
      const dec = (r.decision || '').toUpperCase()
      const reason = (r.reason || '').toUpperCase()
      const passDecision = researchDecision === 'ALL' ? true : dec.includes(researchDecision)
      const passQuery = !q ? true : sym.includes(q) || reason.includes(q)
      return passDecision && passQuery
    })
    if (!researchLatestPerSymbol) return base

    const bySymbol = new Map<string, ResearchRow>()
    for (const row of base) {
      const key = (row.symbol || '').toUpperCase()
      const existing = bySymbol.get(key)
      if (!existing || row.id > existing.id) bySymbol.set(key, row)
    }
    return Array.from(bySymbol.values()).sort((a, b) => b.id - a.id)
  }, [research, researchDecision, researchLatestPerSymbol, researchQuery])

  const researchCounts = useMemo(() => {
    const counts = { BOUGHT: 0, SHORTLISTED: 0, REJECTED: 0, OTHER: 0 }
    for (const r of research) {
      const d = (r.decision || '').toUpperCase()
      if (d.includes('BUY') || d.includes('EXEC') || d.includes('TRADE')) counts.BOUGHT += 1
      else if (d.includes('SHORTLIST') || d.includes('DEFER') || d.includes('ELIGIBLE')) counts.SHORTLISTED += 1
      else if (d.includes('REJECT')) counts.REJECTED += 1
      else counts.OTHER += 1
    }
    return counts
  }, [research])

  const latestResearchPerSymbol = useMemo(() => {
    const bySymbol = new Map<string, ResearchRow>()
    for (const row of research) {
      const key = (row.symbol || '').toUpperCase()
      const existing = bySymbol.get(key)
      if (!existing || row.id > existing.id) bySymbol.set(key, row)
    }
    return Array.from(bySymbol.values()).sort((a, b) => b.id - a.id)
  }, [research])

  const funnel = useMemo(() => {
    const counts = { analysed: latestResearchPerSymbol.length, shortlisted: 0, rejected: 0, bought: 0 }
    for (const r of latestResearchPerSymbol) {
      const d = (r.decision || '').toUpperCase()
      if (d.includes('BUY') || d.includes('EXEC') || d.includes('TRADE')) counts.bought += 1
      else if (d.includes('SHORTLIST') || d.includes('DEFER') || d.includes('ELIGIBLE')) counts.shortlisted += 1
      else if (d.includes('REJECT')) counts.rejected += 1
      else counts.rejected += 1
    }
    return counts
  }, [latestResearchPerSymbol])

  const topCandidates = useMemo(() => {
    const rows = latestResearchPerSymbol.filter((r) => {
      const d = (r.decision || '').toUpperCase()
      return (
        d.includes('SHORTLIST') ||
        d.includes('ELIGIBLE') ||
        d.includes('DEFER') ||
        d.includes('TRADE') ||
        d.includes('BUY') ||
        d.includes('EXEC')
      )
    })
    const scoreOf = (r: ResearchRow) => (r.score === null || r.score === undefined ? -1 : Number(r.score))
    return rows
      .slice()
      .sort((a, b) => scoreOf(b) - scoreOf(a) || b.id - a.id)
      .slice(0, 15)
  }, [latestResearchPerSymbol])

  const filteredEvents = useMemo(() => {
    const q = eventQuery.trim().toUpperCase()
    return events.filter((e) => {
      const lvl = (e.level || '').toUpperCase()
      const sym = (e.symbol || '').toUpperCase()
      const step = (e.step || '').toUpperCase()
      const msg = (e.message || '').toUpperCase()
      const passLevel = eventLevel === 'ALL' ? true : lvl.includes(eventLevel)
      const passQuery = !q ? true : sym.includes(q) || step.includes(q) || msg.includes(q)
      return passLevel && passQuery
    })
  }, [events, eventLevel, eventQuery])

  return (
    <div className="app">
      <div className="topbar">
        <div className="topbar__left">
          <div className="topbar__title">AutoTrader Dashboard</div>
        </div>
        <div className="topbar__right">
          <Pill tone={apiConnected ? 'good' : 'bad'} label={apiConnected ? 'API connected' : 'API offline'} />
          <Pill tone={ibkrPill.tone} label={ibkrPill.label} title={ibkrPill.title} />
          <Pill tone={traderPill.tone} label={traderPill.label} title={traderPill.title} />
          {lastRefresh ? (
            <Pill tone="neutral" label={`Refreshed ${lastRefresh.toLocaleTimeString()}`} />
          ) : (
            <Pill tone="neutral" label="Refreshing…" />
          )}
          <button className="btn btn--ghost" onClick={() => setSettingsOpen(true)}>
            Settings
          </button>
        </div>
      </div>

      {apiError ? (
        <div className="banner">
          <div style={{ fontWeight: 700, marginBottom: 4 }}>Data fetch error</div>
          <div className="mono" style={{ fontSize: 12, opacity: 0.9 }}>
            {apiError}
          </div>
        </div>
      ) : null}

      <div className="grid">
        <div className="stack">
          <Panel
            title="Overview"
            right={
              live?.last_update ? (
                <Pill tone="neutral" label={`Live updated ${formatDateTime(live.last_update)}`} />
              ) : (
                <Pill tone="neutral" label="Live updated —" />
              )
            }
          >
            <div className="kpis">
              <Kpi
                label="Cash (GBP)"
                value={cashRow ? formatMoney(cashRow.value, cashRow.currency) : '—'}
                sub={<span className="muted mono">{cashRow ? cashRow.tag : 'TotalCashValue'}</span>}
              />
              <Kpi
                label="Cash (USD)"
                value={cashUsdRow ? formatMoney(cashUsdRow.value, cashUsdRow.currency) : '—'}
                sub={<span className="muted mono">{cashUsdRow ? cashUsdRow.tag : 'TotalCashValue / CashBalance'}</span>}
              />
              <Kpi
                label="Net liquidation"
                value={netLiqRow ? formatMoney(netLiqRow.value, netLiqRow.currency) : '—'}
                sub={<span className="muted mono">{netLiqRow ? netLiqRow.currency : '—'}</span>}
              />
              <Kpi
                label="Positions value"
                value={grossPosRow ? formatMoney(grossPosRow.value, grossPosRow.currency) : '—'}
                sub={<span className="muted mono">{grossPosRow ? grossPosRow.tag : 'GrossPositionValue'}</span>}
              />
              <Kpi
                label="Unrealised P&L"
                value={
                  <span className={totalUpnl >= 0 ? 'text-green' : 'text-red'}>
                    {formatMoney(totalUpnl, 'USD')}
                  </span>
                }
                sub={<span className="muted">Paper gains/losses</span>}
              />
              <Kpi
                label="Account P&L (since start)"
                value={
                  <span className={(accountPnlDelta ?? 0) >= 0 ? 'text-green' : 'text-red'}>
                    {accountPnlDelta === null
                      ? '—'
                      : `${formatMoney(accountPnlDelta, netLiqRow?.currency ?? 'USD')}${
                          accountPnlDeltaPct === null ? '' : ` (${formatNumber(accountPnlDeltaPct, 2)}%)`
                        }`}
                  </span>
                }
                sub={<span className="muted">&nbsp;</span>}
              />
              <Kpi
                label="Exposure"
                value={
                  <span>
                    {openPositionsCount} pos / {openOrdersSnapshot.length} orders
                  </span>
                }
                sub={<span className="muted">Open positions / open orders</span>}
              />
            </div>
          </Panel>

          <Panel
            className="panelFill"
            bodyClassName="panelBodyFill"
            title="Analysis"
            subtitle="Research, shortlist, and trades"
            right={
              <>
                <Pill tone="neutral" label={`Analysed ${funnel.analysed}`} />
                <Pill tone="warn" label={`Shortlisted ${funnel.shortlisted}`} />
                <Pill tone="good" label={`Bought ${funnel.bought}`} />
                <Pill tone="neutral" label={`Rejected ${funnel.rejected}`} />
              </>
            }
          >
            <div className="tabShell">
              <div className="tabsBar">
                <button
                  className={leftTab === 'research' ? 'tabBtn tabBtn--active' : 'tabBtn'}
                  onClick={() => setLeftTab('research')}
                >
                  Research
                </button>
                <button
                  className={leftTab === 'shortlist' ? 'tabBtn tabBtn--active' : 'tabBtn'}
                  onClick={() => setLeftTab('shortlist')}
                >
                  Shortlist
                </button>
                <button
                  className={leftTab === 'trades' ? 'tabBtn tabBtn--active' : 'tabBtn'}
                  onClick={() => setLeftTab('trades')}
                >
                  Trades
                </button>
                <div className="tabsBar__spacer" />
                <Pill
                  tone="neutral"
                  label={`${live?.current_symbol || '—'} • ${live?.current_step || 'Waiting'}`}
                  title={live?.last_update ? `Last update: ${live.last_update}` : undefined}
                />
              </div>

              <div className="tabContent">
                {leftTab === 'research' ? (
                  <div className="tabPane">
                    <div className="filterBar">
                      <input
                        className="input"
                        placeholder="Filter by symbol or reason…"
                        value={researchQuery}
                        onChange={(e) => setResearchQuery(e.target.value)}
                      />
                      <select
                        className="select"
                        value={researchDecision}
                        onChange={(e) => setResearchDecision(e.target.value)}
                      >
                        <option value="ALL">All decisions</option>
                        <option value="SHORTLIST">Shortlisted</option>
                        <option value="TRADE">Bought</option>
                        <option value="REJECT">Rejected</option>
                      </select>
                      <button className="btn btn--ghost" onClick={() => setResearchLatestPerSymbol((v) => !v)}>
                        {researchLatestPerSymbol ? 'Latest per symbol' : 'All rows'}
                      </button>
                      <button className="btn btn--ghost" onClick={() => setResearchCompact((v) => !v)}>
                        {researchCompact ? 'Detailed columns' : 'Compact columns'}
                      </button>
                      <button
                        className="btn btn--ghost"
                        onClick={() => {
                          setResearchQuery('')
                          setResearchDecision('ALL')
                        }}
                      >
                        Clear
                      </button>
                      <div className="filterBar__spacer" />
                      <div className="hint">
                        Shortlisted <span className="mono">{researchCounts.SHORTLISTED}</span> · Bought{' '}
                        <span className="mono">{researchCounts.BOUGHT}</span> · Rejected <span className="mono">{researchCounts.REJECTED}</span> · Showing{' '}
                        <span className="mono">{filteredResearch.length}</span> /{' '}
                        <span className="mono">{research.length}</span>
                      </div>
                    </div>
                    <div className="tabGrow">
                      <Table
                        columns={researchCompact ? researchColumnsCompact : researchColumnsDetailed}
                        rows={filteredResearch}
                        rowKey={(r) => r.id}
                        emptyText="No research logged yet."
                        height="100%"
                        onRowClick={(r) => setSelectedResearch(r)}
                      />
                    </div>
                  </div>
                ) : null}

                {leftTab === 'shortlist' ? (
                  <div className="tabPane">
                    <div className="hint" style={{ marginBottom: 10 }}>
                      Ranked by <span className="mono">score</span>, using the latest row per symbol.
                    </div>
                    <div className="tabGrow">
                      <Table
                        columns={researchCompact ? researchColumnsCompact : researchColumnsDetailed}
                        rows={topCandidates}
                        rowKey={(r) => r.id}
                        emptyText="No shortlisted candidates yet."
                        height="100%"
                        onRowClick={(r) => setSelectedResearch(r)}
                      />
                    </div>
                  </div>
                ) : null}

                {leftTab === 'trades' ? (
                  <div className="tabPane">
                    <div className="tabGrow">
                      <Table
                        columns={tradesColumns}
                        rows={trades}
                        rowKey={(r) => r.id}
                        emptyText="No trades recorded yet."
                        height="100%"
                      />
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          </Panel>
        </div>

        <div className="stack">
          <Panel
            title="Live activity"
            subtitle="Current cycle step and symbol (updated by the trader)"
            right={
              live?.current_symbol || live?.current_step ? (
                <Pill
                  tone="neutral"
                  label={`${live?.current_symbol || '—'} • ${live?.current_step || '—'}`}
                  title={live?.last_update ? `Last update: ${live.last_update}` : undefined}
                />
              ) : (
                <Pill tone="neutral" label="—" />
              )
            }
          >
            <div className="split">
      <div>
                <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>
                  Current symbol
      </div>
                <div className="mono" style={{ fontSize: 16, fontWeight: 740 }}>
                  {live?.current_symbol || '—'}
                </div>
              </div>
              <div>
                <div className="muted" style={{ fontSize: 11, marginBottom: 4 }}>
                  Current step
                </div>
                <div className="mono" style={{ fontSize: 13, fontWeight: 680 }}>
                  {live?.current_step || '—'}
                </div>
              </div>
            </div>
          </Panel>

          <Panel
            className="panelFill"
            bodyClassName="panelBodyFill"
            title="Monitoring"
            subtitle="Events, positions, orders, and Reddit"
            right={
              <>
                {eventsError ? <Pill tone="bad" label="Stream error" title={eventsError} /> : <Pill tone="good" label="Live stream" />}
                <Pill
                  tone="neutral"
                  label={`${openPositionsCount} pos • ${openOrdersSnapshot.length} orders`}
                  title={`Open orders — Trader snapshot: ${openOrdersSnapshot.length} • IBKR session: ${openOrders.length}`}
                />
              </>
            }
          >
            <div className="tabShell">
              <div className="tabsBar">
                <button
                  className={rightTab === 'events' ? 'tabBtn tabBtn--active' : 'tabBtn'}
                  onClick={() => setRightTab('events')}
                >
                  Events
        </button>
                <button
                  className={rightTab === 'positions' ? 'tabBtn tabBtn--active' : 'tabBtn'}
                  onClick={() => setRightTab('positions')}
                >
                  Positions
                </button>
                <button
                  className={rightTab === 'orders' ? 'tabBtn tabBtn--active' : 'tabBtn'}
                  onClick={() => setRightTab('orders')}
                >
                  Open orders
                </button>
                <button
                  className={rightTab === 'reddit' ? 'tabBtn tabBtn--active' : 'tabBtn'}
                  onClick={() => setRightTab('reddit')}
                >
                  Reddit
                </button>
                <button
                  className={rightTab === 'reviews' ? 'tabBtn tabBtn--active' : 'tabBtn'}
                  onClick={() => setRightTab('reviews')}
                >
                  AI Reviews
                </button>
                <button
                  className={rightTab === 'orderReviews' ? 'tabBtn tabBtn--active' : 'tabBtn'}
                  onClick={() => setRightTab('orderReviews')}
                >
                  Order Reviews
        </button>
                <div className="tabsBar__spacer" />
                {rightTab === 'reddit' ? (
                  <span className="hint">
                    {redditState
                      ? `Fetch UTC ${redditState.last_fetch_utc} · Analysis UTC ${redditState.last_analysis_utc}`
                      : 'Reddit state unavailable'}
                  </span>
                ) : null}
      </div>

              <div className="tabContent">
                {rightTab === 'events' ? (
                  <div className="tabPane">
                    <div className="filterBar">
                      <input
                        className="input"
                        placeholder="Filter by symbol, step, message…"
                        value={eventQuery}
                        onChange={(e) => setEventQuery(e.target.value)}
                      />
                      <select className="select" value={eventLevel} onChange={(e) => setEventLevel(e.target.value)}>
                        <option value="ALL">All levels</option>
                        <option value="INFO">INFO</option>
                        <option value="WARN">WARN</option>
                        <option value="ERROR">ERROR</option>
                      </select>
                      <div className="filterBar__spacer" />
                      <div className="hint">
                        Showing <span className="mono">{filteredEvents.length}</span> / <span className="mono">{events.length}</span>
                      </div>
                    </div>
                    <div className="tabGrow">
                      <Table
                        columns={eventsColumns}
                        rows={filteredEvents}
                        rowKey={(r) => r.id}
                        emptyText="No events yet."
                        height="100%"
                        onRowClick={(r) => setSelectedEvent(r)}
                      />
                    </div>
                  </div>
                ) : null}

                {rightTab === 'positions' ? (
                  <div className="tabPane">
                    <div className="tabGrow">
                      <Table
                        columns={positionsColumns}
                        rows={positions}
                        rowKey={(r) => r.id}
                        emptyText="No positions snapshot yet."
                        height="100%"
                      />
                    </div>
                  </div>
                ) : null}

                {rightTab === 'orders' ? (
                  <div className="tabPane">
                    <div className="filterBar" style={{ justifyContent: 'space-between' }}>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                        <button
                          className={ordersView === 'trader' ? 'tabBtn tabBtn--active' : 'tabBtn'}
                          onClick={() => setOrdersView('trader')}
                        >
                          Trader ({openOrdersSnapshot.length})
                        </button>
                        <button
                          className={ordersView === 'ibkr' ? 'tabBtn tabBtn--active' : 'tabBtn'}
                          onClick={() => setOrdersView('ibkr')}
                        >
                          IBKR ({openOrders.length})
                        </button>
                      </div>
                      {ordersView === 'trader' && openOrdersSnapshot.length ? (
                        <span className="hint">Latest snapshot {formatDateTime(openOrdersSnapshot[0]?.timestamp)}</span>
                      ) : null}
                    </div>
                    <div className="tabGrow">
                      {ordersView === 'trader' ? (
                        <Table
                          columns={ordersSnapshotColumns}
                          rows={openOrdersSnapshot}
                          rowKey={(r) => r.id}
                          emptyText="No open orders in the latest trader snapshot."
                          height="100%"
                        />
                      ) : (
                        <Table
                          columns={ordersColumns}
                          rows={openOrders}
                          rowKey={(r) => r.order_id ?? 0}
                          emptyText="No open orders reported by the API."
                          height="100%"
                        />
                      )}
                    </div>
                  </div>
                ) : null}

                {rightTab === 'reddit' ? (
                  <div className="tabPane">
                    <div className="tabGrow">
                      <Table
                        columns={redditColumns}
                        rows={redditSentiment}
                        rowKey={(r) => r.id}
                        emptyText="No Reddit sentiment recorded yet."
                        height="100%"
                        onRowClick={(r) => setSelectedReddit(r)}
                      />
                    </div>
                  </div>
                ) : null}

                {rightTab === 'reviews' ? (
                  <div className="tabPane">
                    <div className="tabGrow">
                      <Table
                        columns={reviewColumns}
                        rows={positionReviews}
                        rowKey={(r) => r.id}
                        emptyText="No AI position reviews yet."
                        height="100%"
                        onRowClick={(r) => setSelectedReview(r)}
                      />
                    </div>
                  </div>
                ) : null}

                {rightTab === 'orderReviews' ? (
                  <div className="tabPane">
                    <div className="tabGrow">
                      <Table
                        columns={orderReviewColumns}
                        rows={orderReviews}
                        rowKey={(r) => r.id}
                        emptyText="No AI order reviews yet."
                        height="100%"
                        onRowClick={(r) => setSelectedOrderReview(r)}
                      />
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          </Panel>
        </div>
      </div>

      <Modal
        title={selectedResearch?.symbol ? `Research details: ${selectedResearch.symbol}` : 'Research details'}
        isOpen={!!selectedResearch}
        onClose={() => setSelectedResearch(null)}
      >
        {selectedResearch ? (
          <div className="kv">
            <div className="kv__k">Timestamp</div>
            <div className="kv__v mono">{formatDateTime(selectedResearch.timestamp)}</div>
            <div className="kv__k">Symbol</div>
            <div className="kv__v mono">{selectedResearch.symbol || '—'}</div>
            <div className="kv__k">Exchange / currency</div>
            <div className="kv__v mono">
              {(selectedResearch.exchange || '—') + ' / ' + (selectedResearch.currency || '—')}
            </div>
            <div className="kv__k">Price</div>
            <div className="kv__v">{formatMoney(selectedResearch.price, selectedResearch.currency)}</div>
            <div className="kv__k">Indicators</div>
            <div className="kv__v mono">
              RSI {formatNumber(selectedResearch.rsi, 1)} • Vol {formatNumber(selectedResearch.volatility_ratio, 2)}
            </div>
            <div className="kv__k">AI sentiment</div>
            <div className="kv__v mono">{formatNumber(selectedResearch.sentiment_score, 2)}</div>
            <div className="kv__k">Score / rank</div>
            <div className="kv__v mono">
              {formatNumber(selectedResearch.score, 2)} / {formatInteger(selectedResearch.rank)}
            </div>
            <div className="kv__k">Reddit</div>
            <div className="kv__v mono">
              Mentions {formatInteger(selectedResearch.reddit_mentions)} • Sent {formatNumber(selectedResearch.reddit_sentiment, 2)} •
              Conf {formatNumber(selectedResearch.reddit_confidence, 2)} • Override{' '}
              {selectedResearch.reddit_override ? 'YES' : 'NO'}
            </div>
            <div className="kv__k">Decision</div>
            <div className="kv__v">
              <Pill label={decisionLabel(selectedResearch.decision)} tone={decisionTone(selectedResearch.decision)} />
            </div>
            <div className="kv__k">Reason</div>
            <div className="kv__v">{selectedResearch.reason || '—'}</div>
            <div className="kv__k kv__full">AI reasoning</div>
            <div className="kv__v kv__full">
              <div className="pre">{renderKValue(selectedResearch.ai_reasoning)}</div>
            </div>
          </div>
        ) : null}
      </Modal>

      <Modal
        title={selectedEvent?.id ? `Event ${selectedEvent.id}` : 'Event details'}
        isOpen={!!selectedEvent}
        onClose={() => setSelectedEvent(null)}
      >
        {selectedEvent ? (
          <div className="kv">
            <div className="kv__k">Timestamp</div>
            <div className="kv__v mono">{formatDateTime(selectedEvent.timestamp)}</div>
            <div className="kv__k">Level</div>
            <div className="kv__v">
              <Pill label={(selectedEvent.level || '—').toUpperCase()} tone={levelTone(selectedEvent.level)} />
            </div>
            <div className="kv__k">Symbol</div>
            <div className="kv__v mono">{selectedEvent.symbol || '—'}</div>
            <div className="kv__k">Step</div>
            <div className="kv__v mono">{selectedEvent.step || '—'}</div>
            <div className="kv__k kv__full">Message</div>
            <div className="kv__v kv__full">
              <div className="pre">{renderKValue(selectedEvent.message)}</div>
            </div>
          </div>
        ) : null}
      </Modal>

      <Modal
        title={selectedReddit?.symbol ? `Reddit sentiment: ${selectedReddit.symbol}` : 'Reddit sentiment'}
        isOpen={!!selectedReddit}
        onClose={() => setSelectedReddit(null)}
      >
        {selectedReddit ? (
          <div className="kv">
            <div className="kv__k">Timestamp</div>
            <div className="kv__v mono">{formatDateTime(selectedReddit.timestamp)}</div>
            <div className="kv__k">Symbol</div>
            <div className="kv__v mono">{selectedReddit.symbol || '—'}</div>
            <div className="kv__k">Mentions</div>
            <div className="kv__v mono">{formatInteger(selectedReddit.mentions)}</div>
            <div className="kv__k">Sentiment / confidence</div>
            <div className="kv__v mono">
              {formatNumber(selectedReddit.sentiment, 2)} / {formatNumber(selectedReddit.confidence, 2)}
            </div>
            <div className="kv__k kv__full">Rationale</div>
            <div className="kv__v kv__full">
              <div className="pre">{renderKValue(selectedReddit.rationale)}</div>
            </div>
          </div>
        ) : null}
      </Modal>

      <Modal
        title={selectedReview?.symbol ? `Position Review: ${selectedReview.symbol}` : 'Position Review'}
        isOpen={!!selectedReview}
        onClose={() => setSelectedReview(null)}
      >
        {selectedReview ? (
          <div className="kv">
            <div className="kv__k">Timestamp</div>
            <div className="kv__v mono">{formatDateTime(selectedReview.timestamp)}</div>
            <div className="kv__k">Symbol</div>
            <div className="kv__v mono">{selectedReview.symbol || '—'}</div>
            <div className="kv__k">Action</div>
            <div className="kv__v">
              <Pill 
                tone={
                  selectedReview.action === 'SELL' ? 'bad' :
                  selectedReview.action === 'HOLD' ? 'good' :
                  selectedReview.action?.startsWith('ADJUST') ? 'warn' : 'neutral'
                }
                label={selectedReview.action || '—'}
              />
              {selectedReview.executed === 1 && <span className="muted" style={{ marginLeft: 8 }}>✓ Executed</span>}
            </div>
            <div className="kv__k">Entry / Current Price</div>
            <div className="kv__v mono">
              {formatMoney(selectedReview.entry_price ?? 0, selectedReview.currency ?? '')} → {formatMoney(selectedReview.current_price ?? 0, selectedReview.currency ?? '')}
            </div>
            <div className="kv__k">P&L</div>
            <div className="kv__v">
              <span className={selectedReview.pnl_pct && selectedReview.pnl_pct >= 0 ? 'text-green' : 'text-red'}>
                {formatNumber(selectedReview.pnl_pct, 2)}% ({formatMoney(selectedReview.unrealised_pnl ?? 0, selectedReview.currency ?? '')})
              </span>
            </div>
            <div className="kv__k">Minutes Held</div>
            <div className="kv__v mono">{formatInteger(selectedReview.minutes_held)}</div>
            <div className="kv__k">Quantity</div>
            <div className="kv__v mono">{formatInteger(selectedReview.quantity)}</div>
            <div className="kv__k">Current Stop / TP</div>
            <div className="kv__v mono">
              SL: {selectedReview.current_stop_loss ? formatMoney(selectedReview.current_stop_loss, selectedReview.currency ?? '') : '—'} / 
              TP: {selectedReview.current_take_profit ? formatMoney(selectedReview.current_take_profit, selectedReview.currency ?? '') : '—'}
            </div>
            {(selectedReview.action === 'ADJUST_STOP' || selectedReview.action === 'ADJUST_TP') && (
              <>
                <div className="kv__k">New Stop / TP</div>
                <div className="kv__v mono">
                  {selectedReview.new_stop_loss ? `SL: ${formatMoney(selectedReview.new_stop_loss, selectedReview.currency ?? '')}` : ''}
                  {selectedReview.new_take_profit ? `TP: ${formatMoney(selectedReview.new_take_profit, selectedReview.currency ?? '')}` : ''}
                </div>
              </>
            )}
            <div className="kv__k">Confidence / Urgency</div>
            <div className="kv__v mono">
              {formatNumber(selectedReview.confidence, 2)} / {formatNumber(selectedReview.urgency, 2)}
            </div>
            <div className="kv__k kv__full">Rationale</div>
            <div className="kv__v kv__full">
              <div className="pre">{renderKValue(selectedReview.rationale)}</div>
            </div>
            {selectedReview.key_factors && (
              <>
                <div className="kv__k kv__full">Key Factors</div>
                <div className="kv__v kv__full">
                  <div className="pre">{renderKValue(selectedReview.key_factors)}</div>
                </div>
              </>
            )}
          </div>
        ) : null}
      </Modal>

      <Modal
        title={selectedOrderReview?.symbol ? `Order Review: ${selectedOrderReview.symbol}` : 'Order Review'}
        isOpen={!!selectedOrderReview}
        onClose={() => setSelectedOrderReview(null)}
      >
        {selectedOrderReview ? (
          <div className="kv">
            <div className="kv__k">Timestamp</div>
            <div className="kv__v mono">{formatDateTime(selectedOrderReview.timestamp)}</div>
            <div className="kv__k">Symbol</div>
            <div className="kv__v mono">{selectedOrderReview.symbol || '—'}</div>
            <div className="kv__k">Order ID</div>
            <div className="kv__v mono">{selectedOrderReview.order_id || '—'}</div>
            <div className="kv__k">Order Type</div>
            <div className="kv__v mono">{selectedOrderReview.order_action} {selectedOrderReview.order_type}</div>
            <div className="kv__k">Quantity</div>
            <div className="kv__v mono">{formatInteger(selectedOrderReview.order_quantity)}</div>
            <div className="kv__k">AI Action</div>
            <div className="kv__v">
              <Pill 
                tone={
                  selectedOrderReview.action === 'CANCEL' ? 'bad' :
                  selectedOrderReview.action === 'ADJUST_PRICE' ? 'warn' :
                  selectedOrderReview.action === 'KEEP' ? 'good' : 'neutral'
                }
                label={selectedOrderReview.action || '—'}
              />
              {selectedOrderReview.executed === 1 && <span className="muted" style={{ marginLeft: 8 }}>✓ Executed</span>}
            </div>
            <div className="kv__k">Order Price</div>
            <div className="kv__v mono">{formatMoney(selectedOrderReview.order_price ?? 0, '')}</div>
            <div className="kv__k">Current Price</div>
            <div className="kv__v mono">{formatMoney(selectedOrderReview.current_price ?? 0, '')}</div>
            <div className="kv__k">Bid / Ask</div>
            <div className="kv__v mono">
              {formatMoney(selectedOrderReview.bid_price ?? 0, '')} / {formatMoney(selectedOrderReview.ask_price ?? 0, '')}
            </div>
            <div className="kv__k">Price Distance</div>
            <div className="kv__v">
              <span className={selectedOrderReview.price_distance_pct && Math.abs(selectedOrderReview.price_distance_pct) > 3 ? 'text-red' : ''}>
                {formatNumber(selectedOrderReview.price_distance_pct, 2)}%
              </span>
            </div>
            <div className="kv__k">Order Age</div>
            <div className="kv__v mono">{selectedOrderReview.order_age_minutes ?? '—'} minutes</div>
            {selectedOrderReview.action === 'ADJUST_PRICE' && selectedOrderReview.new_price && (
              <>
                <div className="kv__k">New Price</div>
                <div className="kv__v mono text-green">{formatMoney(selectedOrderReview.new_price, '')}</div>
              </>
            )}
            <div className="kv__k">Confidence</div>
            <div className="kv__v mono">{formatNumber(selectedOrderReview.confidence, 2)}</div>
            <div className="kv__k kv__full">Rationale</div>
            <div className="kv__v kv__full">
              <div className="pre">{renderKValue(selectedOrderReview.rationale)}</div>
            </div>
          </div>
        ) : null}
      </Modal>

      <Modal title="Settings" isOpen={settingsOpen} onClose={() => setSettingsOpen(false)}>
        {settingsLoading ? (
          <div className="muted">Loading settings…</div>
        ) : settingsError ? (
          <div>
            <div style={{ fontWeight: 700, marginBottom: 6 }}>Failed to load settings</div>
            <div className="mono" style={{ fontSize: 12, opacity: 0.9 }}>
              {settingsError}
            </div>
          </div>
        ) : settingsDraft && settingsData ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div className="hint">
              Changes are applied by the trader at the start of each cycle (no restart required).
            </div>

            {/* Global trading limits */}
            <div>
              <div style={{ fontWeight: 750, marginBottom: 8 }}>Trading limits</div>
              <div className="filterBar">
                <div className="hint" style={{ minWidth: 190, display: 'flex', alignItems: 'center', gap: 6 }}>
                  Max cash utilisation
                  <InfoIcon tip="Maximum proportion of available cash to allocate at any time. 0.30 means never use more than 30% of your cash." />
                </div>
                <input
                  className="input"
                  type="number"
                  min={0}
                  max={1}
                  step={0.01}
                  value={
                    getDraftOverride(['trading', 'max_cash_utilisation']) ??
                    settingsData.effective?.trading?.max_cash_utilisation ??
                    0
                  }
                  onChange={(e) => {
                    const v = e.target.value
                    if (v === '') return deleteDraftOverride(['trading', 'max_cash_utilisation'])
                    const n = Number(v)
                    if (!Number.isFinite(n)) return
                    setDraftOverride(['trading', 'max_cash_utilisation'], n)
                  }}
                />
              </div>

              <div className="filterBar">
                <div className="hint" style={{ minWidth: 190, display: 'flex', alignItems: 'center', gap: 6 }}>
                  Budget tag
                  <InfoIcon tip="Which IBKR account value to use for cash budgeting. TotalCashValue = settled cash. AvailableFunds = includes margin. CashBalance = raw balance." />
                </div>
                <select
                  className="select"
                  value={
                    getDraftOverride(['trading', 'cash_budget_tag']) ??
                    settingsData.effective?.trading?.cash_budget_tag ??
                    'TotalCashValue'
                  }
                  onChange={(e) => setDraftOverride(['trading', 'cash_budget_tag'], e.target.value)}
                >
                  <option value="TotalCashValue">TotalCashValue</option>
                  <option value="AvailableFunds">AvailableFunds</option>
                  <option value="CashBalance">CashBalance</option>
                </select>
              </div>

              <div className="filterBar">
                <div className="hint" style={{ minWidth: 190, display: 'flex', alignItems: 'center', gap: 6 }}>
                  Min cash reserve (USD)
                  <InfoIcon tip="Minimum USD cash to keep untouched. The trader will not spend if it would reduce USD cash below this amount." />
                </div>
                <input
                  className="input"
                  type="number"
                  min={0}
                  step={100}
                  value={
                    (getDraftOverride(['trading', 'min_cash_reserve_by_currency'])?.USD ??
                      settingsData.effective?.trading?.min_cash_reserve_by_currency?.USD ??
                      0) as number
                  }
                  onChange={(e) => {
                    const v = e.target.value
                    const n = v === '' ? 0 : Number(v)
                    if (!Number.isFinite(n)) return
                    const cur = (getDraftOverride(['trading', 'min_cash_reserve_by_currency']) || {}) as Record<string, number>
                    setDraftOverride(['trading', 'min_cash_reserve_by_currency'], { ...cur, USD: n })
                  }}
                />
              </div>

              <div className="filterBar">
                <div className="hint" style={{ minWidth: 190, display: 'flex', alignItems: 'center', gap: 6 }}>
                  Min cash reserve (GBP)
                  <InfoIcon tip="Minimum GBP cash to keep untouched. The trader will not spend if it would reduce GBP cash below this amount." />
                </div>
                <input
                  className="input"
                  type="number"
                  min={0}
                  step={100}
                  value={
                    (getDraftOverride(['trading', 'min_cash_reserve_by_currency'])?.GBP ??
                      settingsData.effective?.trading?.min_cash_reserve_by_currency?.GBP ??
                      0) as number
                  }
                  onChange={(e) => {
                    const v = e.target.value
                    const n = v === '' ? 0 : Number(v)
                    if (!Number.isFinite(n)) return
                    const cur = (getDraftOverride(['trading', 'min_cash_reserve_by_currency']) || {}) as Record<string, number>
                    setDraftOverride(['trading', 'min_cash_reserve_by_currency'], { ...cur, GBP: n })
                  }}
                />
              </div>
            </div>

            {/* Markets */}
            <div>
              <div style={{ fontWeight: 750, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
                Markets
                <InfoIcon tip="Which stock exchanges to scan for candidates. US = NYSE, NASDAQ, AMEX. UK = LSE." />
              </div>
              {(() => {
                const effMarkets = normaliseMarkets(settingsData.effective?.trading?.markets)
                const markets = normaliseMarkets(getDraftOverride(['trading', 'markets']) ?? effMarkets)
                const toggle = (m: 'US' | 'UK') => {
                  const removing = markets.includes(m)
                  if (removing && markets.length <= 1) return
                  const next = removing ? markets.filter((x) => x !== m) : [...markets, m]
                  setDraftOverride(['trading', 'markets'], next)
                }
                return (
                  <div className="filterBar">
                    <label className="hint" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <input type="checkbox" checked={markets.includes('US')} onChange={() => toggle('US')} />
                      US
                    </label>
                    <label className="hint" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <input type="checkbox" checked={markets.includes('UK')} onChange={() => toggle('UK')} />
                      UK
                    </label>
                  </div>
                )
              })()}
            </div>

            {/* Risk & filters */}
            <div>
              <div style={{ fontWeight: 750, marginBottom: 8 }}>Risk &amp; filters</div>

              <div className="filterBar">
                <div className="hint" style={{ minWidth: 190, display: 'flex', alignItems: 'center', gap: 6 }}>
                  Risk per trade
                  <InfoIcon tip="Maximum percentage of equity to risk on a single trade. Used to calculate position size based on stop-loss distance." />
                </div>
                <input
                  className="input"
                  type="number"
                  min={0}
                  max={1}
                  step={0.01}
                  value={getDraftOverride(['trading', 'risk_per_trade']) ?? settingsData.effective?.trading?.risk_per_trade ?? 0}
                  onChange={(e) => {
                    const v = e.target.value
                    if (v === '') return deleteDraftOverride(['trading', 'risk_per_trade'])
                    const n = Number(v)
                    if (!Number.isFinite(n)) return
                    setDraftOverride(['trading', 'risk_per_trade'], n)
                  }}
                />
              </div>

              <div className="filterBar">
                <div className="hint" style={{ minWidth: 190, display: 'flex', alignItems: 'center', gap: 6 }}>
                  Max positions
                  <InfoIcon tip="Maximum number of open positions allowed at once. Once reached, the trader won't open new positions." />
                </div>
                <input
                  className="input"
                  type="number"
                  min={0}
                  step={1}
                  value={getDraftOverride(['trading', 'max_positions']) ?? settingsData.effective?.trading?.max_positions ?? 0}
                  onChange={(e) => {
                    const v = e.target.value
                    if (v === '') return deleteDraftOverride(['trading', 'max_positions'])
                    const n = Number(v)
                    if (!Number.isFinite(n)) return
                    setDraftOverride(['trading', 'max_positions'], Math.max(0, Math.floor(n)))
                  }}
                />
              </div>

              <div className="filterBar">
                <div className="hint" style={{ minWidth: 190, display: 'flex', alignItems: 'center', gap: 6 }}>
                  Max new per cycle
                  <InfoIcon tip="Maximum number of new positions to open in a single trading cycle. Prevents opening too many positions at once." />
                </div>
                <input
                  className="input"
                  type="number"
                  min={0}
                  step={1}
                  value={
                    getDraftOverride(['trading', 'max_new_positions_per_cycle']) ??
                    settingsData.effective?.trading?.max_new_positions_per_cycle ??
                    0
                  }
                  onChange={(e) => {
                    const v = e.target.value
                    if (v === '') return deleteDraftOverride(['trading', 'max_new_positions_per_cycle'])
                    const n = Number(v)
                    if (!Number.isFinite(n)) return
                    setDraftOverride(['trading', 'max_new_positions_per_cycle'], Math.max(0, Math.floor(n)))
                  }}
                />
              </div>

              <div className="filterBar">
                <div className="hint" style={{ minWidth: 190, display: 'flex', alignItems: 'center', gap: 6 }}>
                  Max share price
                  <InfoIcon tip="Only consider stocks priced at or below this amount. Filters out expensive stocks during the screening phase." />
                </div>
                <input
                  className="input"
                  type="number"
                  min={0}
                  step={0.1}
                  value={getDraftOverride(['trading', 'max_share_price']) ?? settingsData.effective?.trading?.max_share_price ?? 0}
                  onChange={(e) => {
                    const v = e.target.value
                    if (v === '') return deleteDraftOverride(['trading', 'max_share_price'])
                    const n = Number(v)
                    if (!Number.isFinite(n)) return
                    setDraftOverride(['trading', 'max_share_price'], n)
                  }}
                />
              </div>

              <div className="filterBar">
                <div className="hint" style={{ minWidth: 190, display: 'flex', alignItems: 'center', gap: 6 }}>
                  Min share price
                  <InfoIcon tip="Only consider stocks priced at or above this amount. Helps avoid ultra-low priced stocks that are often illiquid or restricted." />
                </div>
                <input
                  className="input"
                  type="number"
                  min={0}
                  step={0.1}
                  value={getDraftOverride(['trading', 'min_share_price']) ?? settingsData.effective?.trading?.min_share_price ?? 0}
                  onChange={(e) => {
                    const v = e.target.value
                    if (v === '') return deleteDraftOverride(['trading', 'min_share_price'])
                    const n = Number(v)
                    if (!Number.isFinite(n)) return
                    setDraftOverride(['trading', 'min_share_price'], n)
                  }}
                />
              </div>

              <div className="filterBar">
                <div className="hint" style={{ minWidth: 190, display: 'flex', alignItems: 'center', gap: 6 }}>
                  Min average volume
                  <InfoIcon tip="Only consider stocks whose scanner-reported volume is above this threshold. Helps avoid illiquid microcaps." />
                </div>
                <input
                  className="input"
                  type="number"
                  min={0}
                  step={10000}
                  value={getDraftOverride(['trading', 'min_avg_volume']) ?? settingsData.effective?.trading?.min_avg_volume ?? 0}
                  onChange={(e) => {
                    const v = e.target.value
                    if (v === '') return deleteDraftOverride(['trading', 'min_avg_volume'])
                    const n = Number(v)
                    if (!Number.isFinite(n)) return
                    setDraftOverride(['trading', 'min_avg_volume'], Math.max(0, Math.floor(n)))
                  }}
                />
              </div>

              <div className="filterBar">
                <div className="hint" style={{ minWidth: 190, display: 'flex', alignItems: 'center', gap: 6 }}>
                  Exclude microcaps
                  <InfoIcon tip="Exclude trading classes that commonly trigger microcap compliance restrictions (e.g. Rule 144). Recommended to keep enabled." />
                </div>
                <label className="hint" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <input
                    type="checkbox"
                    checked={
                      (getDraftOverride(['trading', 'exclude_microcap']) ??
                        settingsData.effective?.trading?.exclude_microcap ??
                        false) as boolean
                    }
                    onChange={(e) => {
                      const checked = e.target.checked
                      setDraftOverride(['trading', 'exclude_microcap'], checked)
                    }}
                  />
                  Enabled
                </label>
              </div>

              <div className="filterBar">
                <div className="hint" style={{ minWidth: 190, display: 'flex', alignItems: 'center', gap: 6 }}>
                  Volatility threshold
                  <InfoIcon tip="Minimum ATR/Price ratio required for a stock to be considered 'volatile enough'. Higher = need more price movement." />
                </div>
                <input
                  className="input"
                  type="number"
                  min={0}
                  step={0.01}
                  value={
                    getDraftOverride(['trading', 'volatility_threshold']) ??
                    settingsData.effective?.trading?.volatility_threshold ??
                    0
                  }
                  onChange={(e) => {
                    const v = e.target.value
                    if (v === '') return deleteDraftOverride(['trading', 'volatility_threshold'])
                    const n = Number(v)
                    if (!Number.isFinite(n)) return
                    setDraftOverride(['trading', 'volatility_threshold'], n)
                  }}
                />
              </div>
            </div>

            {/* Screener / symbol universe */}
            <div>
              <div style={{ fontWeight: 750, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
                Screener (symbols to analyse)
                <InfoIcon tip="Controls how the trader chooses which symbols to analyse before the AI runs. These settings affect the universe size and composition." />
              </div>

              <div className="filterBar">
                <div className="hint" style={{ minWidth: 190, display: 'flex', alignItems: 'center', gap: 6 }}>
                  Max candidates
                  <InfoIcon tip="Maximum number of unique symbols to send through the analysis pipeline each cycle. Lower values reduce API usage and cycle time." />
                </div>
                <input
                  className="input"
                  type="number"
                  min={1}
                  step={1}
                  value={
                    getDraftOverride(['trading', 'screener', 'max_candidates']) ??
                    settingsData.effective?.trading?.screener?.max_candidates ??
                    250
                  }
                  onChange={(e) => {
                    const v = e.target.value
                    if (v === '') return deleteDraftOverride(['trading', 'screener', 'max_candidates'])
                    const n = Number(v)
                    if (!Number.isFinite(n)) return
                    setDraftOverride(['trading', 'screener', 'max_candidates'], Math.max(1, Math.floor(n)))
                  }}
                />
              </div>

              <div className="filterBar">
                <div className="hint" style={{ minWidth: 190, display: 'flex', alignItems: 'center', gap: 6 }}>
                  Include Reddit symbols
                  <InfoIcon tip="When enabled, tickers mentioned in cached Reddit posts are added to the analysis universe (if IBKR can qualify them). Excludes always win." />
                </div>
                <label className="hint" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <input
                    type="checkbox"
                    checked={
                      (getDraftOverride(['trading', 'screener', 'include_reddit_symbols']) ??
                        settingsData.effective?.trading?.screener?.include_reddit_symbols ??
                        false) as boolean
                    }
                    onChange={(e) => setDraftOverride(['trading', 'screener', 'include_reddit_symbols'], e.target.checked)}
                  />
                  Enabled
                </label>
              </div>

              <div className="filterBar" style={{ alignItems: 'flex-start' }}>
                <div className="hint" style={{ minWidth: 190, display: 'flex', alignItems: 'center', gap: 6, paddingTop: 8 }}>
                  Scan codes
                  <InfoIcon tip="IBKR scanner categories used to find candidates. One per line (e.g. MOST_ACTIVE, TOP_PERC_GAIN). Leave empty to disable scanner discovery and rely only on Include symbols." />
                </div>
                <textarea
                  className="textarea"
                  rows={4}
                  placeholder={'MOST_ACTIVE\nTOP_PERC_GAIN\nHOT_BY_VOLUME\nHIGH_VS_13W_HI'}
                  value={listToLines(getDraftOverride(['trading', 'screener', 'scan_codes']) ?? settingsData.effective?.trading?.screener?.scan_codes ?? [])}
                  onChange={(e) => {
                    const lines = linesToList(e.target.value).map((x) => x.toUpperCase())
                    if (lines.length === 0) return setDraftOverride(['trading', 'screener', 'scan_codes'], [])
                    setDraftOverride(['trading', 'screener', 'scan_codes'], lines)
                  }}
                />
              </div>

              <div className="filterBar" style={{ alignItems: 'flex-start' }}>
                <div className="hint" style={{ minWidth: 190, display: 'flex', alignItems: 'center', gap: 6, paddingTop: 8 }}>
                  Include symbols
                  <InfoIcon tip="Optional manual symbols to add into the universe each cycle. One per line. Use either SYMBOL or SYMBOL,US / SYMBOL,UK." />
                </div>
                <textarea
                  className="textarea"
                  rows={4}
                  placeholder={'AAPL,US\nVOD,UK'}
                  value={listToLines(getDraftOverride(['trading', 'screener', 'include_symbols']) ?? settingsData.effective?.trading?.screener?.include_symbols ?? [])}
                  onChange={(e) => {
                    const lines = linesToList(e.target.value)
                    if (lines.length === 0) return deleteDraftOverride(['trading', 'screener', 'include_symbols'])
                    setDraftOverride(['trading', 'screener', 'include_symbols'], lines)
                  }}
                />
              </div>

              <div className="filterBar" style={{ alignItems: 'flex-start' }}>
                <div className="hint" style={{ minWidth: 190, display: 'flex', alignItems: 'center', gap: 6, paddingTop: 8 }}>
                  Exclude symbols
                  <InfoIcon tip="Symbols to always exclude from analysis/trading (one per line). Excludes override Includes." />
                </div>
                <textarea
                  className="textarea"
                  rows={4}
                  placeholder={'GME\nAMC'}
                  value={listToLines(getDraftOverride(['trading', 'screener', 'exclude_symbols']) ?? settingsData.effective?.trading?.screener?.exclude_symbols ?? [])}
                  onChange={(e) => {
                    const lines = linesToList(e.target.value).map((x) => x.split(',', 1)[0].toUpperCase())
                    if (lines.length === 0) return deleteDraftOverride(['trading', 'screener', 'exclude_symbols'])
                    setDraftOverride(['trading', 'screener', 'exclude_symbols'], lines)
                  }}
                />
              </div>
            </div>

            {/* Strategy */}
            <div>
              <div style={{ fontWeight: 750, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
                Strategy
                <InfoIcon tip="AI prompts that define the trading behaviour. Create multiple strategies to experiment with different approaches." />
              </div>
              <div className="filterBar">
                <div className="hint" style={{ minWidth: 190, display: 'flex', alignItems: 'center', gap: 6 }}>
                  Active strategy
                  <InfoIcon tip="The strategy currently in use. Changes take effect at the start of the next trading cycle." />
                </div>
                <select
                  className="select"
                  value={settingsDraft.active_strategy ?? 'Default'}
                  onChange={(e) => setSettingsDraft({ ...settingsDraft, active_strategy: e.target.value })}
                >
                  {settingsDraft.strategies.map((s) => (
                    <option key={s.name} value={s.name}>
                      {s.name}
                    </option>
                  ))}
                </select>
                <button
                  className="btn btn--ghost"
                  onClick={() => {
                    let i = 1
                    const existing = new Set(settingsDraft.strategies.map((s) => s.name))
                    while (existing.has(`Strategy ${i}`)) i += 1
                    const name = `Strategy ${i}`
                    setSettingsDraft({
                      ...settingsDraft,
                      strategies: [...settingsDraft.strategies, { name, overrides: {} }],
                      active_strategy: name,
                    })
                  }}
                >
                  + New
                </button>
                {settingsDraft.active_strategy && settingsDraft.active_strategy !== 'Default' ? (
                  <button
                    className="btn btn--ghost"
                    onClick={() => {
                      const name = settingsDraft.active_strategy as string
                      const nextStrategies = settingsDraft.strategies.filter((s) => s.name !== name)
                      const nextActive = nextStrategies[0]?.name || 'Default'
                      setSettingsDraft({ ...settingsDraft, strategies: nextStrategies, active_strategy: nextActive })
                    }}
                  >
                    Delete
                  </button>
                ) : null}
              </div>

              {activeStrategy && promptTemplates ? (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 14, marginTop: 12 }}>
                  {/* Shortlist prompt */}
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                      <span style={{ fontWeight: 600 }}>Shortlist prompt</span>
                      <InfoIcon tip="How the AI decides which candidates to keep for comparison. Runs per symbol during the scan. Candidates that pass are shortlisted for the final buy decision." />
                    </div>
                    <textarea
                      className="input"
                      style={{ height: 180, padding: '10px', width: '100%', resize: 'vertical' }}
                      value={
                        getActiveStrategyOverride(['ai', 'shortlist_system_prompt']) ??
                        (activeStrategy.name === 'Default' ? promptTemplates.shortlist : '')
                      }
                      placeholder={activeStrategy.name === 'Default' ? '' : 'Leave empty to use the Default strategy prompt'}
                      onChange={(e) => {
                        const v = e.target.value
                        if (activeStrategy.name === 'Default') {
                          // Default always has a value
                          setActiveStrategyOverride(['ai', 'shortlist_system_prompt'], v)
                        } else {
                          if (v.trim() === '') return deleteActiveStrategyOverride(['ai', 'shortlist_system_prompt'])
                          setActiveStrategyOverride(['ai', 'shortlist_system_prompt'], v)
                        }
                      }}
                    />
                  </div>

                  {/* Buy selection prompt */}
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                      <span style={{ fontWeight: 600 }}>Buy selection prompt</span>
                      <InfoIcon tip="How the AI picks which shortlisted candidates to actually buy. Runs once at the end of each cycle with all shortlisted candidates for comparison." />
                    </div>
                    <textarea
                      className="input"
                      style={{ height: 160, padding: '10px', width: '100%', resize: 'vertical' }}
                      value={
                        getActiveStrategyOverride(['ai', 'buy_selection_system_prompt']) ??
                        (activeStrategy.name === 'Default' ? promptTemplates.buy_selection : '')
                      }
                      placeholder={activeStrategy.name === 'Default' ? '' : 'Leave empty to use the Default strategy prompt'}
                      onChange={(e) => {
                        const v = e.target.value
                        if (activeStrategy.name === 'Default') {
                          setActiveStrategyOverride(['ai', 'buy_selection_system_prompt'], v)
                        } else {
                          if (v.trim() === '') return deleteActiveStrategyOverride(['ai', 'buy_selection_system_prompt'])
                          setActiveStrategyOverride(['ai', 'buy_selection_system_prompt'], v)
                        }
                      }}
                    />
                  </div>

                  {/* Position review prompt */}
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                      <span style={{ fontWeight: 600 }}>Position review prompt</span>
                      <InfoIcon tip="How the AI manages open positions. Decides whether to HOLD, SELL immediately, or ADJUST stop-loss/take-profit levels for each position." />
                    </div>
                    <textarea
                      className="input"
                      style={{ height: 160, padding: '10px', width: '100%', resize: 'vertical' }}
                      value={
                        getActiveStrategyOverride(['ai', 'position_review_system_prompt']) ??
                        (activeStrategy.name === 'Default' ? promptTemplates.position_review : '')
                      }
                      placeholder={activeStrategy.name === 'Default' ? '' : 'Leave empty to use the Default strategy prompt'}
                      onChange={(e) => {
                        const v = e.target.value
                        if (activeStrategy.name === 'Default') {
                          setActiveStrategyOverride(['ai', 'position_review_system_prompt'], v)
                        } else {
                          if (v.trim() === '') return deleteActiveStrategyOverride(['ai', 'position_review_system_prompt'])
                          setActiveStrategyOverride(['ai', 'position_review_system_prompt'], v)
                        }
                      }}
                    />
                  </div>

                  {/* Order review prompt */}
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                      <span style={{ fontWeight: 600 }}>Order review prompt</span>
                      <InfoIcon tip="How the AI manages open orders. Decides whether to KEEP waiting, CANCEL if stale, or ADJUST the limit/stop price to improve fill probability." />
                    </div>
                    <textarea
                      className="input"
                      style={{ height: 160, padding: '10px', width: '100%', resize: 'vertical' }}
                      value={
                        getActiveStrategyOverride(['ai', 'order_review_system_prompt']) ??
                        (activeStrategy.name === 'Default' ? promptTemplates.order_review : '')
                      }
                      placeholder={activeStrategy.name === 'Default' ? '' : 'Leave empty to use the Default strategy prompt'}
                      onChange={(e) => {
                        const v = e.target.value
                        if (activeStrategy.name === 'Default') {
                          setActiveStrategyOverride(['ai', 'order_review_system_prompt'], v)
                        } else {
                          if (v.trim() === '') return deleteActiveStrategyOverride(['ai', 'order_review_system_prompt'])
                          setActiveStrategyOverride(['ai', 'order_review_system_prompt'], v)
                        }
                      }}
                    />
                  </div>
                </div>
              ) : null}
            </div>

            {/* Feature toggles */}
            <div>
              <div style={{ fontWeight: 750, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 8 }}>
                Features
                <InfoIcon tip="Enable or disable optional trading features." />
              </div>
              <div className="filterBar">
                <label className="hint" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <input
                    type="checkbox"
                    checked={
                      (getDraftOverride(['reddit', 'enabled']) ?? settingsData.effective?.reddit?.enabled ?? true) as boolean
                    }
                    onChange={(e) => setDraftOverride(['reddit', 'enabled'], e.target.checked)}
                  />
                  Reddit
                  <InfoIcon tip="Fetch sentiment from Reddit to boost or override AI decisions for stocks trending on WallStreetBets, stocks, etc." />
                </label>
                <label className="hint" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <input
                    type="checkbox"
                    checked={
                      (getDraftOverride(['intraday', 'enabled']) ?? settingsData.effective?.intraday?.enabled ?? true) as boolean
                    }
                    onChange={(e) => setDraftOverride(['intraday', 'enabled'], e.target.checked)}
                  />
                  Intraday cycle
                  <InfoIcon tip="Run the main trading loop that scans markets, analyses candidates, and executes trades." />
                </label>
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 10, marginTop: 6 }}>
              {settingsError ? <span className="badText">{settingsError}</span> : null}
              <button
                className="btn"
                disabled={settingsSaving}
                onClick={() => {
                  if (!settingsDraft) return
                  setSettingsSaving(true)
                  setSettingsError(null)
                  void api
                    .runtimeConfigPut(settingsDraft)
                    .then((nextRuntime) => api.configEffective().then((d) => ({ nextRuntime, d })))
                    .then(({ d }) => {
                      setSettingsData(d)
                      setSettingsDraft(d.runtime)
                      setSettingsError(null)
                    })
                    .catch((e) => {
                      setSettingsError(api.toErrorMessage(e))
                    })
                    .finally(() => setSettingsSaving(false))
                }}
              >
                {settingsSaving ? 'Saving…' : 'Save settings'}
              </button>
            </div>
          </div>
        ) : (
          <div className="muted">Settings unavailable.</div>
        )}
      </Modal>
    </div>
  )
}

export default App

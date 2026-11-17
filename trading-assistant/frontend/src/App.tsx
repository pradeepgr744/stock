import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import axios from 'axios'
import { useMemo, useState } from 'react'

type HealthResponse = {
  status: string
  service: string
  environment: string
  database: { status: string; error?: string }
}
type IntegrationStatus = {
  upstox: { configured: boolean }
  telegram: { configured: boolean }
}
type ScannerStatus = {
  running: boolean
  connection: string
  message: string
  symbols: string[]
  last_message_at: string | null
  last_alert_at: string | null
  last_error: string | null
}
type NewsArticle = {
  article_id: string
  title: string
  source: string
  symbols: string[]
  age_minutes: number
  sentiment_score: number
  sentiment_label: string
  event_severity: string
  source_trust: string
  reasons: string[]
}
type NewsAssessment = {
  status: string
  coverage_status: string
  feed_count: number
  article_count: number
  state: 'NORMAL' | 'CAUTION' | 'BLOCKED'
  halt_new_entries: boolean
  halt_all_symbols: boolean
  blocked_symbols: string[]
  sentiment_score: number
  reasons: string[]
  articles: NewsArticle[]
  feed_errors: string[]
  limitations: string[]
}
type DashboardStatus = {
  health: HealthResponse
  integrations: IntegrationStatus
  scanner: ScannerStatus
}

const SAMPLE_TRADE_HINT = {
  symbol: 'RELIANCE',
  strategy: 'DEMO ONLY',
  direction: 'BUY',
  entry: 2850,
  stop_loss: 2825,
  target: 2905,
  quantity: 1,
  reason: 'Test notification. Not a live trading signal.',
}

async function fetchStatus(): Promise<DashboardStatus> {
  const [health, integrations, scanner] = await Promise.all([
    axios.get<HealthResponse>('/api/health'),
    axios.get<IntegrationStatus>('/api/integrations/status'),
    axios.get<ScannerStatus>('/api/scanner/status'),
  ])
  return { health: health.data, integrations: integrations.data, scanner: scanner.data }
}
const timeLabel = (value?: string | null) => value ? new Date(value).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }) : 'Not received yet'
const errorMessage = (error: unknown) => axios.isAxiosError(error)
  ? String(error.response?.data?.detail ?? error.message)
  : error instanceof Error ? error.message : 'Unknown error'

function Metric({ label, value, detail, tone = 'neutral' }: { label: string; value: string; detail?: string; tone?: 'good' | 'warn' | 'bad' | 'neutral' }) {
  const tones = { good: 'text-emerald-300', warn: 'text-amber-300', bad: 'text-rose-300', neutral: 'text-slate-100' }
  return <div className="rounded-xl border border-white/[0.07] bg-[#111923] p-4">
    <p className="text-xs font-medium uppercase tracking-[.14em] text-slate-500">{label}</p>
    <p className={`mt-3 text-xl font-semibold ${tones[tone]}`}>{value}</p>
    {detail && <p className="mt-1 text-xs text-slate-500">{detail}</p>}
  </div>
}
function StatusDot({ active, warning = false }: { active: boolean; warning?: boolean }) {
  return <span className={`inline-block h-2 w-2 rounded-full ${active ? 'bg-emerald-400' : warning ? 'bg-amber-400' : 'bg-slate-600'}`} />
}
function SectionTitle({ eyebrow, title, note }: { eyebrow: string; title: string; note?: string }) {
  return <div className="mb-4"><p className="text-[10px] font-semibold uppercase tracking-[.2em] text-cyan-400">{eyebrow}</p><h2 className="mt-1 text-lg font-semibold text-white">{title}</h2>{note && <p className="mt-1 text-xs text-slate-500">{note}</p>}</div>
}

function App() {
  const queryClient = useQueryClient()
  const [activeTab, setActiveTab] = useState<'overview' | 'news' | 'architecture'>('overview')
  const { data, isLoading, error, dataUpdatedAt } = useQuery({
    queryKey: ['dashboard-status'],
    queryFn: fetchStatus,
    refetchInterval: 5000,
    retry: 1,
  })
  const news = useMutation({
    mutationFn: () => axios.post<NewsAssessment>('/api/research/news/assess', { symbols: [] }).then(r => r.data),
  })
  const startScanner = useMutation({
    mutationFn: () => axios.post('/api/scanner/start'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard-status'] }),
  })
  const stopScanner = useMutation({
    mutationFn: () => axios.post('/api/scanner/stop'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard-status'] }),
  })
  const telegram = useMutation({ mutationFn: () => axios.post('/api/notifications/test', SAMPLE_TRADE_HINT) })
  const health = data?.health
  const scanner = data?.scanner
  const upstoxReady = Boolean(data?.integrations.upstox.configured)
  const telegramReady = Boolean(data?.integrations.telegram.configured)
  const pipeline = useMemo(() => [
    { name: 'FastAPI backend', state: health?.status === 'ok' ? 'Connected' : health ? 'Degraded' : 'Waiting', done: health?.status === 'ok' },
    { name: 'PostgreSQL / Neon', state: health?.database?.status ?? 'Unknown', done: health?.database?.status === 'connected' },
    { name: 'Upstox credentials', state: upstoxReady ? 'Configured' : 'Missing', done: upstoxReady },
    { name: 'Upstox live stream', state: scanner?.connection ?? 'Not started', done: scanner?.connection === 'connected' },
    { name: 'Telegram bot', state: telegramReady ? 'Configured' : 'Missing', done: telegramReady },
    { name: 'News RSS assessment', state: news.data?.coverage_status ?? 'Not checked', done: news.data?.coverage_status === 'AVAILABLE' },
  ], [health, upstoxReady, scanner, telegramReady, news.data])

  return <main className="min-h-screen bg-[#080d13] text-slate-200">
    <div className="mx-auto max-w-[1440px] px-4 py-5 sm:px-7 lg:px-10">
      <header className="flex flex-col gap-5 border-b border-white/[.08] pb-5 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex items-center gap-3">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl border border-cyan-400/20 bg-cyan-400/10 text-lg font-bold text-cyan-300">N</div>
          <div><p className="text-[10px] font-bold uppercase tracking-[.24em] text-cyan-400">NSE · INTRADAY SYSTEM</p><h1 className="text-xl font-semibold tracking-tight text-white">Market Operations</h1></div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-lg border border-white/10 bg-white/[.03] px-3 py-2 text-xs text-slate-400">IST · {new Date().toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata' })}</span>
          <span className="rounded-lg border border-emerald-500/20 bg-emerald-500/[.07] px-3 py-2 text-xs font-medium text-emerald-300">MANUAL EXECUTION</span>
          <span className="rounded-lg border border-white/10 px-3 py-2 text-xs text-slate-400">₹50,000 research capital</span>
        </div>
      </header>

      <nav className="flex gap-1 border-b border-white/[.08] py-3">
        {(['overview', 'news', 'architecture'] as const).map(tab => <button key={tab} onClick={() => setActiveTab(tab)} className={`rounded-lg px-4 py-2 text-xs font-semibold capitalize transition ${activeTab === tab ? 'bg-cyan-400/10 text-cyan-300' : 'text-slate-500 hover:bg-white/[.04] hover:text-slate-200'}`}>{tab === 'overview' ? 'Live overview' : tab === 'news' ? 'News & events' : 'System architecture'}</button>)}
        <div className="ml-auto hidden items-center gap-2 text-[11px] text-slate-600 sm:flex"><span className="h-1.5 w-1.5 rounded-full bg-cyan-400" /> Status refreshes every 5 seconds</div>
      </nav>

      {activeTab === 'overview' && <>
        <section className="grid gap-3 py-5 sm:grid-cols-2 xl:grid-cols-4">
          <Metric label="Backend API" value={isLoading ? 'Checking' : health?.status ?? 'Unavailable'} detail={health?.environment ?? 'Waiting for response'} tone={health?.status === 'ok' ? 'good' : 'bad'} />
          <Metric label="Database" value={health?.database?.status ?? 'Unknown'} detail="Connection health only" tone={health?.database?.status === 'connected' ? 'good' : 'warn'} />
          <Metric label="Market feed" value={scanner?.connection ?? 'Not started'} detail={scanner?.running ? 'Scanner process running' : 'Live scanner stopped'} tone={scanner?.connection === 'connected' ? 'good' : 'warn'} />
          <Metric label="News entry gate" value={news.data?.coverage_status === 'UNAVAILABLE' ? 'FEED UNAVAILABLE' : news.data?.state ?? 'Not checked'} detail={news.data?.coverage_status === 'UNAVAILABLE' ? 'Entries paused because no usable news source is available' : news.data?.coverage_status ?? 'Run assessment to check feeds'} tone={news.data?.coverage_status === 'UNAVAILABLE' || news.data?.state === 'BLOCKED' ? 'bad' : news.data?.state === 'CAUTION' ? 'warn' : 'neutral'} />
        </section>

        {error && <div className="mb-4 rounded-xl border border-rose-500/20 bg-rose-500/[.06] p-4 text-sm text-rose-300">Backend request failed: {errorMessage(error)}. Check VITE API proxy / backend URL.</div>}

        <section className="grid gap-4 xl:grid-cols-[1.55fr_1fr]">
          <div className="rounded-2xl border border-white/[.08] bg-[#0e151e] p-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
              <div><SectionTitle eyebrow="Execution monitor" title="Live ORB scanner" note="Existing live implementation · configured symbols are currently NIFTY 50 defaults, not the full NIFTY 200 universe." />
                <div className="flex items-center gap-2 text-sm"><StatusDot active={scanner?.connection === 'connected'} warning={scanner?.running && scanner?.connection !== 'connected'} /><span className="font-medium text-white">{scanner?.message ?? 'Waiting for backend status'}</span></div>
              </div>
              <div className="flex gap-2">
                <button onClick={() => startScanner.mutate()} disabled={Boolean(scanner?.running) || startScanner.isPending || !upstoxReady || !telegramReady} className="rounded-lg bg-emerald-400 px-3 py-2 text-xs font-bold text-[#07110d] disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-500">{startScanner.isPending ? 'Starting…' : 'Start scanner'}</button>
                <button onClick={() => stopScanner.mutate()} disabled={!scanner?.running || stopScanner.isPending} className="rounded-lg border border-white/10 px-3 py-2 text-xs font-semibold text-slate-300 disabled:cursor-not-allowed disabled:opacity-40">{stopScanner.isPending ? 'Stopping…' : 'Stop'}</button>
              </div>
            </div>
            <div className="mt-5 grid gap-3 sm:grid-cols-3">
              <Metric label="Symbols subscribed" value={String(scanner?.symbols?.length ?? 0)} detail="Resolved at scanner start" />
              <Metric label="Last feed message" value={scanner?.last_message_at ? timeLabel(scanner.last_message_at) : 'No message'} detail="Timestamp from scanner process" />
              <Metric label="Last alert" value={timeLabel(scanner?.last_alert_at)} detail="Telegram alert timestamp" />
            </div>
            {scanner?.last_error && <p className="mt-3 rounded-lg bg-rose-500/[.07] p-3 text-xs text-rose-300">Scanner error: {scanner.last_error}</p>}
            {startScanner.error && <p className="mt-3 text-xs text-rose-300">Start failed: {errorMessage(startScanner.error)}</p>}
            {stopScanner.error && <p className="mt-3 text-xs text-rose-300">Stop failed: {errorMessage(stopScanner.error)}</p>}
            <div className="mt-4 rounded-xl border border-amber-400/15 bg-amber-400/[.05] p-3 text-xs leading-5 text-amber-200/80">Scanner currently evaluates the opening-range breakout rule only. It does not yet enforce the portfolio risk module, use the news assessment as an entry gate, or create an order. A connected feed is not proof that a valid setup exists.</div>
          </div>

          <div className="rounded-2xl border border-white/[.08] bg-[#0e151e] p-5">
            <SectionTitle eyebrow="Service dependencies" title="Backend service map" note="Live configuration and process state, not a profitability indicator." />
            <div className="space-y-3">{pipeline.map(item => <div key={item.name} className="flex items-center justify-between gap-3 border-b border-white/[.05] pb-3 last:border-0"><div className="flex items-center gap-2"><StatusDot active={item.done} warning={!item.done} /><span className="text-sm text-slate-300">{item.name}</span></div><span className={`text-xs ${item.done ? 'text-emerald-300' : 'text-amber-300'}`}>{item.state}</span></div>)}</div>
            <p className="mt-3 text-[11px] text-slate-600">Credentials marked configured are only checked for presence; token validity is verified only when an API call succeeds.</p>
          </div>
        </section>

        <section className="mt-4 grid gap-4 xl:grid-cols-2">
          <div className="rounded-2xl border border-white/[.08] bg-[#0e151e] p-5">
            <SectionTitle eyebrow="Risk policy · configured research limits" title="Intraday guardrails" note="The Python risk module exists but is not connected to live orders or positions yet." />
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <Metric label="Capital" value="₹50,000" /><Metric label="Risk / trade" value="₹250" /><Metric label="Open risk cap" value="₹500" /><Metric label="Max positions" value="2" /><Metric label="Daily loss stop" value="₹750" /><Metric label="Square-off" value="15:15 IST" />
            </div>
            <p className="mt-3 text-xs text-amber-300">Policy is documented and unit-tested; live enforcement is not active. Do not interpret these limits as protection on live positions.</p>
          </div>
          <div className="rounded-2xl border border-white/[.08] bg-[#0e151e] p-5">
            <SectionTitle eyebrow="Manual operations" title="Notifications & news check" note="Actions call the existing backend endpoints; no trades are submitted." />
            <div className="flex flex-wrap gap-2">
              <button onClick={() => news.mutate()} disabled={news.isPending} className="rounded-lg border border-cyan-400/20 bg-cyan-400/10 px-3 py-2 text-xs font-semibold text-cyan-300 disabled:opacity-50">{news.isPending ? 'Checking feeds…' : 'Run news assessment'}</button>
              <button onClick={() => telegram.mutate()} disabled={!telegramReady || telegram.isPending} className="rounded-lg border border-white/10 px-3 py-2 text-xs font-semibold text-slate-300 disabled:opacity-40">{telegram.isPending ? 'Sending…' : 'Send demo Telegram alert'}</button>
            </div>
            {news.error && <p className="mt-3 text-xs text-rose-300">News check failed: {errorMessage(news.error)}</p>}
            {telegram.error && <p className="mt-3 text-xs text-rose-300">Telegram failed: {errorMessage(telegram.error)}</p>}
            {telegram.data && <p className="mt-3 text-xs text-emerald-300">Demo alert sent by backend.</p>}
            {news.data && <p className="mt-3 text-xs text-slate-400">Latest news result: {news.data.coverage_status} · {news.data.article_count} articles · {news.data.state}. Open News & Events for details.</p>}
          </div>
        </section>

        <section className="mt-4 rounded-2xl border border-white/[.08] bg-[#0e151e] p-5">
          <SectionTitle eyebrow="Transparency" title="What is not live yet" />
          <div className="grid gap-3 text-xs leading-5 text-slate-400 sm:grid-cols-2 xl:grid-cols-4">
            <p className="rounded-lg bg-white/[.025] p-3"><b className="text-slate-200">NIFTY 200 scanner</b><br />Current live scanner defaults to a NIFTY 50 list.</p>
            <p className="rounded-lg bg-white/[.025] p-3"><b className="text-slate-200">Trade candidates table</b><br />No candidate history/list API is currently exposed.</p>
            <p className="rounded-lg bg-white/[.025] p-3"><b className="text-slate-200">Portfolio risk</b><br />Risk module is not wired to broker positions or scanner entries.</p>
            <p className="rounded-lg bg-white/[.025] p-3"><b className="text-slate-200">News monitoring</b><br />News is fetched only when you click assess; no background worker or persistence.</p>
          </div>
        </section>
      </>}

      {activeTab === 'news' && <section className="py-5">
        <div className="mb-5 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between"><div><SectionTitle eyebrow="Catalyst monitor" title="News & event risk" note="On-demand RSS assessment. Not a real-time news terminal or directional predictor." /></div><button onClick={() => news.mutate()} disabled={news.isPending} className="rounded-lg bg-cyan-400 px-4 py-2 text-xs font-bold text-slate-950 disabled:opacity-50">{news.isPending ? 'Fetching official feeds…' : 'Refresh news assessment'}</button></div>
        {news.error && <div className="rounded-xl border border-rose-500/20 bg-rose-500/[.06] p-4 text-sm text-rose-300">{errorMessage(news.error)}</div>}
        {!news.data && !news.isPending && <div className="rounded-2xl border border-dashed border-white/10 p-10 text-center"><p className="text-sm text-slate-300">No news assessment has been run in this session.</p><p className="mt-2 text-xs text-slate-500">Run assessment to fetch configured RSS/Atom feeds through the backend.</p></div>}
        {news.data && <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4"><Metric label="Coverage" value={news.data.coverage_status} detail={`${news.data.feed_count} configured feeds`} tone={news.data.coverage_status === 'AVAILABLE' ? 'good' : 'warn'} /><Metric label="Entry gate" value={news.data.coverage_status === 'UNAVAILABLE' ? 'FEED UNAVAILABLE' : news.data.state} detail={news.data.coverage_status === 'UNAVAILABLE' ? 'Fail-closed safety pause; not a detected market event' : news.data.halt_new_entries ? 'New entries paused by event assessment' : 'No hard pause returned'} tone={news.data.coverage_status === 'UNAVAILABLE' || news.data.state === 'BLOCKED' ? 'bad' : news.data.state === 'CAUTION' ? 'warn' : 'neutral'} /><Metric label="Articles" value={String(news.data.article_count)} detail="After freshness and deduplication" /><Metric label="Blocked symbols" value={news.data.blocked_symbols.join(', ') || 'None'} detail={news.data.coverage_status === 'UNAVAILABLE' ? 'All new entries paused: feed unavailable' : news.data.halt_all_symbols ? 'All symbols paused by market event' : 'Issuer-specific blocks'} /></div>
          {news.data.reasons.length > 0 && <div className="mt-4 rounded-xl border border-amber-400/15 bg-amber-400/[.05] p-4"><p className="text-xs font-semibold text-amber-200">Assessment reasons</p>{news.data.reasons.map((reason, i) => <p key={i} className="mt-2 text-xs text-amber-100/70">{reason}</p>)}</div>}
          <div className="mt-4 overflow-hidden rounded-2xl border border-white/[.08] bg-[#0e151e]"><div className="border-b border-white/[.07] p-4"><h3 className="text-sm font-semibold text-white">Detected articles</h3></div>{news.data.articles.length === 0 ? <p className="p-6 text-sm text-slate-500">No fresh articles returned by the configured feeds.</p> : <div className="divide-y divide-white/[.06]">{news.data.articles.map(article => <article key={article.article_id} className="p-4"><div className="flex flex-wrap items-center gap-2"><span className="rounded bg-slate-700/50 px-2 py-1 text-[10px] text-slate-300">{article.source}</span><span className="text-[10px] text-slate-500">{Math.round(article.age_minutes)} min ago</span><span className="text-[10px] text-cyan-300">{article.event_severity}</span><span className="text-[10px] text-slate-500">{article.source_trust}</span></div><h4 className="mt-2 text-sm font-medium text-slate-100">{article.title}</h4><p className="mt-1 text-xs text-slate-500">{article.sentiment_label} · score {article.sentiment_score} · {article.symbols.join(', ') || 'No symbol tag'}</p>{article.reasons.map((r, i) => <p key={i} className="mt-1 text-xs text-slate-500">{r}</p>)}</article>)}</div>}</div>
          {news.data.feed_errors.length > 0 && <div className="mt-4 rounded-xl border border-rose-500/20 p-4 text-xs text-rose-300">{news.data.feed_errors.map((e, i) => <p key={i}>{e}</p>)}</div>}
          <div className="mt-4 rounded-xl border border-white/[.07] p-4 text-xs text-slate-500">{news.data.limitations.map((v, i) => <p key={i} className="mt-1">{v}</p>)}</div>
        </>}
      </section>}

      {activeTab === 'architecture' && <section className="py-5">
        <SectionTitle eyebrow="System map" title="Current architecture & data flow" note="Solid lines are implemented request paths; dashed/amber blocks are planned or not connected." />
        <div className="grid gap-3 lg:grid-cols-3">
          <div className="rounded-xl border border-cyan-400/20 bg-cyan-400/[.04] p-4"><p className="text-xs font-semibold text-cyan-300">WEB CLIENT · React + Vite</p><p className="mt-2 text-xs leading-5 text-slate-400">Dashboard · TanStack Query · Axios · Tailwind · status polling every 5 seconds · on-demand news assessment</p></div>
          <div className="rounded-xl border border-emerald-400/20 bg-emerald-400/[.04] p-4"><p className="text-xs font-semibold text-emerald-300">API · FastAPI</p><p className="mt-2 text-xs leading-5 text-slate-400">Health · Upstox market data · scanner controls/status · Telegram test alert · research/news assessment</p></div>
          <div className="rounded-xl border border-white/10 bg-white/[.025] p-4"><p className="text-xs font-semibold text-slate-200">EXTERNAL SERVICES</p><p className="mt-2 text-xs leading-5 text-slate-400">Upstox REST + Market Data Feed V3 WebSocket · Telegram Bot API · PostgreSQL/Neon connection</p></div>
        </div>
        <div className="my-3 grid gap-3 md:grid-cols-2">
          <div className="rounded-xl border border-emerald-400/20 bg-[#0e151e] p-4"><p className="text-xs font-semibold text-emerald-300">IMPLEMENTED PATH</p><p className="mt-2 text-xs leading-5 text-slate-400">React → FastAPI → Upstox instrument master / REST quotes / WebSocket → ORB rule → Telegram alert. Dashboard polls scanner status.</p></div>
          <div className="rounded-xl border border-amber-400/20 bg-amber-400/[.035] p-4"><p className="text-xs font-semibold text-amber-300">NOT YET CONNECTED</p><p className="mt-2 text-xs leading-5 text-slate-400">News assessment → live scanner gate · portfolio risk → positions/orders · persistent event/trade ledger · multi-position portfolio backtest · NIFTY 200 universe.</p></div>
        </div>
        <div className="rounded-2xl border border-white/[.08] bg-[#0e151e] p-5"><h3 className="text-sm font-semibold text-white">Backend capability matrix</h3><div className="mt-4 grid gap-2 sm:grid-cols-2">{[
          ['Health & database connectivity','Implemented'],['Upstox credentials presence check','Implemented'],['LTP / full quote / candles / market status','Implemented'],['Instrument master refresh & search','Implemented'],['Live ORB WebSocket scanner','Implemented · NIFTY 50 defaults'],['Telegram test / scanner alerts','Implemented'],['News RSS assessment endpoint','Implemented · on demand'],['Intraday risk rules','Research module only'],['ORB + VWAP + sweep/reclaim live signals','ORB only'],['NIFTY 200 scanner','Not implemented'],['Background news worker & event storage','Not implemented'],['Risk enforcement on live positions','Not implemented'],['Automatic order placement','Intentionally disabled'],
        ].map(([name,state]) => <div key={name} className="flex items-center justify-between gap-3 rounded-lg bg-white/[.025] px-3 py-2"><span className="text-xs text-slate-400">{name}</span><span className={`shrink-0 text-[10px] ${state.startsWith('Implemented') ? 'text-emerald-300' : state === 'Research module only' ? 'text-amber-300' : 'text-slate-500'}`}>{state}</span></div>)}</div></div>
      </section>}

      <footer className="mt-8 flex flex-col gap-2 border-t border-white/[.07] py-5 text-[10px] text-slate-600 sm:flex-row sm:items-center sm:justify-between"><span>Trading Assistant · Research & manual execution only</span><span>Last dashboard response: {dataUpdatedAt ? new Date(dataUpdatedAt).toLocaleTimeString('en-IN') : 'Not available'} · No orders are placed</span></footer>
    </div>
  </main>
}
export default App

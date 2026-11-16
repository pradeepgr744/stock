import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import axios from 'axios'

const SAMPLE_TRADE_HINT = {
  symbol: 'RELIANCE',
  strategy: 'VWAP breakout',
  direction: 'BUY',
  entry: 2850.0,
  stop_loss: 2825.0,
  target: 2905.0,
  quantity: 4,
  reason: 'Trend remains strong and price is holding above VWAP',
}

interface HealthResponse {
  status: string
  service: string
  environment: string
  database: {
    status: string
    database_url: string
    error?: string
  }
}

interface IntegrationStatus {
  upstox: { configured: boolean }
  telegram: { configured: boolean }
}

interface ScannerStatus {
  running: boolean
  connection: string
  message: string
  symbols: string[]
  last_message_at: string | null
  last_alert_at: string | null
  last_error: string | null
}

interface DashboardStatus {
  health: HealthResponse
  integrations: IntegrationStatus
  scanner: ScannerStatus
}

async function fetchStatus(): Promise<DashboardStatus> {
  const [health, integrations, scanner] = await Promise.all([
    axios.get<HealthResponse>('/api/health'),
    axios.get<IntegrationStatus>('/api/integrations/status'),
    axios.get<ScannerStatus>('/api/scanner/status'),
  ])
  return { health: health.data, integrations: integrations.data, scanner: scanner.data }
}

function App() {
  const queryClient = useQueryClient()
  const { data, isLoading, error } = useQuery({
    queryKey: ['dashboard-status'],
    queryFn: fetchStatus,
    refetchInterval: 5000,
  })

  const { mutate, isPending, data: result, error: submitError } = useMutation({
    mutationFn: () => axios.post('/api/notifications/test', SAMPLE_TRADE_HINT),
  })
  const startScanner = useMutation({
    mutationFn: () => axios.post('/api/scanner/start'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard-status'] }),
  })
  const stopScanner = useMutation({
    mutationFn: () => axios.post('/api/scanner/stop'),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['dashboard-status'] }),
  })

  const health = data?.health

  return (
    <main className="min-h-screen bg-slate-950 text-slate-50">
      <div className="mx-auto max-w-6xl px-6 py-10">
        <header className="mb-8 flex items-center justify-between border-b border-slate-800 pb-6">
          <div>
            <p className="text-sm uppercase tracking-[0.24em] text-cyan-400">Phase 2</p>
            <h1 className="mt-2 text-3xl font-bold">Trading Assistant Dashboard</h1>
          </div>
          <span className="rounded-full border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 text-xs font-semibold text-emerald-300">
            Manual execution mode
          </span>
        </header>

        <section className="grid gap-6 sm:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-2xl border border-slate-800 bg-slate-900 p-5 shadow-glow">
            <p className="text-sm text-slate-400">Backend status</p>
            <p className="mt-3 text-2xl font-semibold text-cyan-300">
              {isLoading ? 'Checking...' : health?.status ?? 'Unavailable'}
            </p>
          </div>
          <div className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
            <p className="text-sm text-slate-400">Environment</p>
            <p className="mt-3 text-2xl font-semibold text-violet-300">
              {health?.environment ?? 'development'}
            </p>
          </div>
          <div className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
            <p className="text-sm text-slate-400">Database</p>
            <p className="mt-3 text-2xl font-semibold text-emerald-300">
              {health?.database?.status ?? 'pending'}
            </p>
          </div>
          <div className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
            <p className="text-sm text-slate-400">Upstox market data</p>
            <p className="mt-3 text-2xl font-semibold text-amber-300">
              {data?.integrations.upstox.configured ? 'Configured' : 'Not configured'}
            </p>
          </div>
          <div className="rounded-2xl border border-slate-800 bg-slate-900 p-5">
            <p className="text-sm text-slate-400">Telegram alerts</p>
            <p className="mt-3 text-2xl font-semibold text-amber-300">
              {data?.integrations.telegram.configured ? 'Configured' : 'Not configured'}
            </p>
          </div>
        </section>

        <section className="mt-8 rounded-2xl border border-slate-800 bg-slate-900 p-6">
          <h2 className="text-xl font-semibold text-white">System snapshot</h2>

          {error ? (
            <p className="mt-4 text-red-300">
              Backend unavailable: {(error as Error).message}
            </p>
          ) : (
            <div className="mt-4 space-y-3 text-sm text-slate-300">
              <p>
                <span className="font-medium text-slate-100">Service:</span> {health?.service ?? 'Loading...'}
              </p>
              <p>
                <span className="font-medium text-slate-100">Database URL:</span>{' '}
                {health?.database?.database_url ?? 'Checking connection...'}
              </p>
              {health?.database?.error ? (
                <p className="text-amber-300">Connection error: {health.database.error}</p>
              ) : null}
              <p className="text-slate-400">
                Live ORB scanning can be started below when market data and Telegram are configured.
                Scanner rules are experimental candidates, not verified recommendations. No orders
                are placed by this app.
              </p>
            </div>
          )}
        </section>

        <section className="mt-8 rounded-2xl border border-amber-600/40 bg-slate-900 p-6">
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">Live ORB candidate scanner</h2>
              <p className="mt-2 text-sm text-slate-300">
                Scans the configured NIFTY 50 symbols using the Upstox stream. Alerts are rule-based
                candidates for your manual review, not guaranteed trades.
              </p>
            </div>
            <div className="flex gap-3">
              <button
                type="button"
                onClick={() => startScanner.mutate()}
                disabled={Boolean(data?.scanner.running) || startScanner.isPending || !data?.integrations.upstox.configured || !data?.integrations.telegram.configured}
                className="rounded-xl bg-emerald-500 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-emerald-400 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
              >
                {startScanner.isPending ? 'Starting...' : 'Start live scan'}
              </button>
              <button
                type="button"
                onClick={() => stopScanner.mutate()}
                disabled={!data?.scanner.running || stopScanner.isPending}
                className="rounded-xl border border-slate-600 px-4 py-2 text-sm font-semibold text-slate-200 transition hover:bg-slate-800 disabled:cursor-not-allowed disabled:text-slate-500"
              >
                {stopScanner.isPending ? 'Stopping...' : 'Stop scan'}
              </button>
            </div>
          </div>

          <div className="mt-4 grid gap-3 text-sm text-slate-300 sm:grid-cols-2">
            <p><span className="font-medium text-slate-100">Scanner:</span> {data?.scanner.connection ?? 'Checking...'}</p>
            <p><span className="font-medium text-slate-100">Status:</span> {data?.scanner.message ?? 'Checking...'}</p>
            <p><span className="font-medium text-slate-100">Symbols:</span> {data?.scanner.symbols.length ?? 0}</p>
            <p><span className="font-medium text-slate-100">Last feed message:</span> {data?.scanner.last_message_at ?? 'None yet'}</p>
            <p><span className="font-medium text-slate-100">Last Telegram alert:</span> {data?.scanner.last_alert_at ?? 'None yet'}</p>
          </div>
          {data?.scanner.last_error ? (
            <p className="mt-3 text-sm text-red-300">Scanner error: {data.scanner.last_error}</p>
          ) : null}
          {startScanner.error ? (
            <p className="mt-3 text-sm text-red-300">
              Could not start scanner:{' '}
              {axios.isAxiosError(startScanner.error) && startScanner.error.response?.data?.detail
                ? startScanner.error.response.data.detail
                : startScanner.error.message}
            </p>
          ) : null}
          {stopScanner.error ? (
            <p className="mt-3 text-sm text-red-300">
              Could not stop scanner:{' '}
              {axios.isAxiosError(stopScanner.error) && stopScanner.error.response?.data?.detail
                ? stopScanner.error.response.data.detail
                : stopScanner.error.message}
            </p>
          ) : null}
          <div className="mt-4 rounded-lg border border-amber-700/40 bg-amber-950/30 p-3 text-sm text-amber-200">
            Starter rules: opening range 9:15–9:30 IST; wait for a completed 5-minute close outside
            the range; breakout volume must match or exceed the opening-range 5-minute average;
            illustrative stop at the opposite range boundary and target at 2R. These rules are
            unvalidated, do not size positions, and do not guarantee a profit. Keep the backend
            running while scanning.
          </div>
        </section>

        <section className="mt-8 rounded-2xl border border-cyan-600/40 bg-slate-900 p-6">
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div>
              <h2 className="text-xl font-semibold text-white">Telegram trade alert</h2>
              <p className="mt-2 text-sm text-slate-300">
                Send a sample trade message that matches the format used for manual execution hints.
              </p>
            </div>
            <button
              type="button"
              onClick={() => mutate()}
              disabled={isPending || !data?.integrations.telegram.configured}
              className="rounded-xl bg-cyan-500 px-4 py-2 text-sm font-semibold text-slate-950 transition hover:bg-cyan-400 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400"
            >
              {isPending ? 'Sending...' : 'Send sample Telegram alert'}
            </button>
          </div>

          <div className="mt-4 rounded-xl border border-slate-800 bg-slate-950 p-4 text-sm text-slate-300">
            <p className="font-medium text-slate-100">Sample payload:</p>
            <pre className="mt-2 overflow-x-auto whitespace-pre-wrap text-cyan-200">
{`{
  "symbol": "RELIANCE",
  "strategy": "VWAP breakout",
  "direction": "BUY",
  "entry": 2850.0,
  "stop_loss": 2825.0,
  "target": 2905.0,
  "quantity": 4,
  "reason": "Trend remains strong and price is holding above VWAP"
}`}
            </pre>
          </div>

          {submitError ? (
            <p className="mt-4 text-sm text-red-300">
              Telegram send failed:{' '}
              {axios.isAxiosError(submitError) && submitError.response?.data?.detail
                ? submitError.response.data.detail
                : (submitError as Error).message}
            </p>
          ) : null}

          {result?.data?.status === 'sent' ? (
            <p className="mt-4 text-sm text-emerald-300">{result.data.message}</p>
          ) : null}

          {!data?.integrations.telegram.configured ? (
            <p className="mt-4 text-sm text-amber-300">
              Configure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in backend/.env before sending.
            </p>
          ) : null}
        </section>
      </div>
    </main>
  )
}

export default App

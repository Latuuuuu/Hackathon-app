import { useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { api, latestStatus } from '../api/client'
import type { BtStatus, RunSummary } from '../api/types'
import { RunTimeline } from '../components/RunTimeline'
import { StatusBadge } from '../components/StatusBadge'
import { rememberPrompt } from '../lib/store'
import { usePolling } from '../lib/usePolling'

/** A submitted prompt waiting for the cloud to start a tree on bt_engine. */
export interface Tracking {
  prompt: string
  baseline: string | null // latest run_id before submitting
  since: number // ms epoch
  runId?: string
}

// bt_engine and the browser may disagree on the clock; accept runs started a bit "before" submit.
const CLOCK_SLACK_MS = 60_000

export function TaskPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [prompt, setPrompt] = useState('')
  const [tracking, setTracking] = useState<Tracking | null>(
    (location.state as { tracking?: Tracking } | null)?.tracking ?? null,
  )
  const [status, setStatus] = useState<BtStatus | null>(null)
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')

  // Consume the hand-off from the feedback page once, so going "back" later doesn't re-track.
  useEffect(() => {
    if (location.state) navigate('.', { replace: true, state: null })
  }, [location.state, navigate])

  const active = !!tracking || status?.state === 'running'

  usePolling(
    async () => {
      try {
        const s = tracking?.runId ? await api.btStatus(tracking.runId) : await latestStatus()
        setError('')
        if (tracking && !tracking.runId && s && s.run_id !== tracking.baseline && s.started_at * 1000 >= tracking.since - CLOCK_SLACK_MS) {
          rememberPrompt(s.run_id, tracking.prompt)
          setTracking({ ...tracking, runId: s.run_id })
        }
        setStatus(s)
        if (s && tracking?.runId === s.run_id && s.state !== 'running') {
          setTracking(null)
          navigate(`/feedback/${encodeURIComponent(s.run_id)}`)
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    },
    active ? 500 : 3000,
  )

  usePolling(async () => {
    try {
      setRuns((await api.btRuns()).slice().reverse().slice(0, 8))
    } catch {
      /* shown by the status poll */
    }
  }, 5000)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    const text = prompt.trim()
    if (!text) return
    setBusy(true)
    setError('')
    setInfo('')
    try {
      const baseline = (await latestStatus().catch(() => null))?.run_id ?? null
      const res = await api.submitTask({ prompt: text })
      if (res.mock && !res.executed) {
        setInfo('已記錄指令（雲端為 mock 模式，沒有實際執行）。')
        setTracking(null)
      } else {
        if (res.run_id) rememberPrompt(res.run_id, text)
        setTracking({ prompt: text, baseline, since: Date.now(), runId: res.run_id })
        setInfo('已送出，等待機器人開始執行…')
      }
      setPrompt('')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function cancel() {
    if (!confirm('確定要中斷目前的任務？機器人會停下所有動作。')) return
    try {
      const r = await api.btCancel()
      setInfo(r.was_running ? '已送出中斷。' : '目前沒有執行中的任務。')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const waiting = tracking && !tracking.runId
  const shown = waiting ? null : status

  return (
    <div className="page">
      <section className="card">
        <h2>發布任務</h2>
        <form onSubmit={submit} className="stack">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            placeholder="例如：把桌上的紅色杯子拿過來"
            rows={3}
            maxLength={1000}
            disabled={busy}
          />
          <button type="submit" className="primary" disabled={busy || !prompt.trim()}>
            {busy ? '送出中…' : '發布任務'}
          </button>
        </form>
        {info && <p className="info">{info}</p>}
        {error && <p className="error">{error}</p>}
      </section>

      <section className="card">
        <div className="row between">
          <h2>任務狀態</h2>
          <button className="danger" onClick={cancel} disabled={status?.state !== 'running'}>
            中斷任務
          </button>
        </div>
        {waiting && (
          <div className="waiting">
            <span className="spinner" />
            <div>
              <div>等待雲端產生行為樹…</div>
              <small className="muted">「{tracking.prompt}」</small>
            </div>
            <button className="link" onClick={() => setTracking(null)}>
              不等了
            </button>
          </div>
        )}
        {!waiting && !shown && <p className="muted">還沒有任何任務紀錄。</p>}
        {shown && (
          <>
            <div className="row status-line">
              <StatusBadge state={shown.state} />
              <code>{shown.run_id}</code>
              <span className="muted">{shown.elapsed_s?.toFixed(1)} 秒</span>
            </div>
            {shown.state === 'running' && (
              <p>
                目前動作：<b>{shown.running_leaves.length ? shown.running_leaves.join('、') : '—'}</b>
              </p>
            )}
            {shown.state !== 'running' && (
              <Link to={`/feedback/${encodeURIComponent(shown.run_id)}`}>查看結果與回饋 →</Link>
            )}
            <details open={shown.state === 'running'}>
              <summary>執行紀錄</summary>
              <RunTimeline trace={shown.trace ?? []} truncated={shown.trace_truncated} />
            </details>
          </>
        )}
      </section>

      {runs.length > 0 && (
        <section className="card">
          <h2>最近的任務</h2>
          <ul className="runs">
            {runs.map((r) => (
              <li key={r.run_id}>
                <Link to={`/feedback/${encodeURIComponent(r.run_id)}`}>
                  <code>{r.run_id}</code>
                  <StatusBadge state={r.state} />
                  <span className="muted">{new Date(r.started_at * 1000).toLocaleTimeString('zh-TW')}</span>
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  )
}

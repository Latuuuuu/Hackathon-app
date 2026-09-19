import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, noteText } from '../api/client'
import type { BtStatus, GripForce } from '../api/types'
import { RunTimeline } from '../components/RunTimeline'
import { StatusBadge } from '../components/StatusBadge'
import { promptFor } from '../lib/store'
import { usePolling } from '../lib/usePolling'
import type { Tracking } from './TaskPage'

const GRIP_OPTIONS: { value: GripForce; label: string }[] = [
  { value: 'too_weak', label: '太小（沒夾住／滑掉）' },
  { value: 'ok', label: '剛好' },
  { value: 'too_strong', label: '太大（夾壞／變形）' },
]

export function FeedbackPage() {
  const { runId = '' } = useParams()
  const navigate = useNavigate()
  const [status, setStatus] = useState<BtStatus | null>(null)
  const [loadError, setLoadError] = useState('')

  usePolling(
    async () => {
      try {
        setStatus(await api.btStatus(runId, true))
        setLoadError('')
      } catch (e) {
        setLoadError(e instanceof Error ? e.message : String(e))
      }
    },
    1000,
    !status || status.state === 'running',
  )

  const failed = status && status.state !== 'success' && status.state !== 'running'

  return (
    <div className="page">
      <Link to="/" className="back">
        ← 回任務
      </Link>
      <section className="card">
        <div className="row between">
          <h2>任務結果</h2>
          {status && <StatusBadge state={status.state} />}
        </div>
        {loadError && <p className="error">{loadError}</p>}
        {!status && !loadError && <p className="muted">載入中…</p>}
        {status && (
          <>
            <p className="muted">
              <code>{status.run_id}</code>・{status.elapsed_s?.toFixed(1)} 秒
              {promptFor(runId) && <>・「{promptFor(runId)}」</>}
            </p>
            {status.state === 'running' && <p>任務仍在執行中，結束後這裡會自動更新。</p>}
            {status.state === 'success' && <p className="ok-text">任務完成！請留下回饋，幫助機器人下次做得更好。</p>}
            {failed && <FailureDetails status={status} />}
            <details>
              <summary>完整執行紀錄</summary>
              <RunTimeline trace={status.trace ?? []} truncated={status.trace_truncated} />
            </details>
          </>
        )}
      </section>

      {failed && <RetryForm status={status} runId={runId} onSent={(t) => navigate('/', { state: { tracking: t } })} />}
      {status && status.state !== 'running' && <FeedbackForm status={status} />}
    </div>
  )
}

function FailureDetails({ status }: { status: BtStatus }) {
  return (
    <div className="failure">
      <h3>{status.state === 'canceled' ? '任務被中斷' : '失敗原因'}</h3>
      {status.error && <pre className="error-box">{status.error}</pre>}
      {status.notes.length > 0 ? (
        <ul>
          {status.notes.map((n, i) => (
            <li key={i}>
              <b>{n.node}</b>：{n.message}
            </li>
          ))}
        </ul>
      ) : (
        !status.error && <p className="muted">{status.state === 'canceled' ? '由使用者或新的任務中斷。' : '沒有回報具體原因。'}</p>
      )}
      {status.last_leaf_failure && (
        <p className="muted">
          最後失敗的步驟：<code>{status.last_leaf_failure.node}</code>（{status.last_leaf_failure.type}）
        </p>
      )}
    </div>
  )
}

function RetryForm({ status, runId, onSent }: { status: BtStatus; runId: string; onSent: (t: Tracking) => void }) {
  const [prompt, setPrompt] = useState(promptFor(runId))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    const text = prompt.trim()
    if (!text) return
    setBusy(true)
    setError('')
    try {
      const since = Date.now()
      const res = await api.submitTask({
        prompt: text,
        retry_of: { run_id: status.run_id, notes: status.notes.map(noteText).concat(status.error ? [status.error] : []) },
      })
      if (res.mock && !res.executed) {
        setInfo('已記錄新指令（雲端為 mock 模式，沒有實際執行）。')
      } else {
        onSent({ prompt: text, baseline: status.run_id, since, runId: res.run_id })
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="card">
      <h2>修改指令重來</h2>
      <p className="muted">失敗原因會一併送給雲端，讓它重新規劃。</p>
      <form onSubmit={submit} className="stack">
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} rows={3} maxLength={1000} placeholder="輸入新的指令" />
        <button type="submit" className="primary" disabled={busy || !prompt.trim()}>
          {busy ? '送出中…' : '重新發布'}
        </button>
      </form>
      {info && <p className="info">{info}</p>}
      {error && <p className="error">{error}</p>}
    </section>
  )
}

function FeedbackForm({ status }: { status: BtStatus }) {
  const [grip, setGrip] = useState<GripForce | undefined>()
  const [rating, setRating] = useState<number | undefined>()
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(false)
  const [error, setError] = useState('')

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await api.sendFeedback({
        run_id: status.run_id,
        outcome: status.state,
        grip_force: grip,
        rating,
        comment: comment.trim(),
      })
      setSent(true)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (sent) {
    return (
      <section className="card">
        <h2>回饋</h2>
        <p className="ok-text">謝謝！回饋已送出。</p>
      </section>
    )
  }

  return (
    <section className="card">
      <h2>回饋</h2>
      <form onSubmit={submit} className="stack">
        <fieldset>
          <legend>夾取力道</legend>
          <div className="segmented">
            {GRIP_OPTIONS.map((o) => (
              <label key={o.value} className={grip === o.value ? 'on' : ''}>
                <input type="radio" name="grip" value={o.value} checked={grip === o.value} onChange={() => setGrip(o.value)} />
                {o.label}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend>整體滿意度</legend>
          <div className="stars" role="radiogroup">
            {[1, 2, 3, 4, 5].map((n) => (
              <button
                type="button"
                key={n}
                role="radio"
                aria-checked={rating === n}
                aria-label={`${n} 顆星`}
                className={rating && n <= rating ? 'on' : ''}
                onClick={() => setRating(n)}
              >
                ★
              </button>
            ))}
          </div>
        </fieldset>
        <label className="stack">
          <span>其他意見</span>
          <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={3} maxLength={2000} placeholder="例如：接近杯子時太快" />
        </label>
        <button type="submit" className="primary" disabled={busy || (!grip && !rating && !comment.trim())}>
          {busy ? '送出中…' : '送出回饋'}
        </button>
      </form>
      {error && <p className="error">{error}</p>}
    </section>
  )
}

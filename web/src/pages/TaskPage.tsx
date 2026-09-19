import { useEffect, useRef, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { api, latestStatus } from '../api/client'
import type { BtStatus, ChatReply, RunSummary } from '../api/types'
import { ChatPanel } from '../components/ChatPanel'
import { ConfirmCard } from '../components/ConfirmCard'
import { RunTimeline } from '../components/RunTimeline'
import { StatusBadge } from '../components/StatusBadge'
import { type ChatState, loadChat, rememberPrompt, saveChat } from '../lib/store'
import { usePolling } from '../lib/usePolling'

/** A mission the user confirmed; followed on bt_engine by run_id. */
interface Tracking {
  missionId: string
  prompt: string
  runId?: string
  // Fallback when the cloud did not return a run_id: the first run newer than this one.
  baseline: string | null
  since: number
}

/** Hand-off from the feedback page: a message to send in the current conversation. */
export interface TaskPageState {
  send?: string
}

// bt_engine and the browser may disagree on the clock; accept runs started a bit "before" execute.
const CLOCK_SLACK_MS = 60_000

function errText(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export function TaskPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [chat, setChatState] = useState<ChatState>(loadChat)
  const [draft, setDraft] = useState('')
  const [thinkingSince, setThinkingSince] = useState<number | null>(null)
  const [pending, setPending] = useState<ChatReply | null>(null)
  const [executing, setExecuting] = useState(false)
  const [tracking, setTracking] = useState<Tracking | null>(null)
  const [status, setStatus] = useState<BtStatus | null>(null)
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [statusError, setStatusError] = useState('')
  const handedOff = useRef(false)

  function setChat(next: ChatState) {
    setChatState(next)
    saveChat(next)
  }

  // Offer the latest plan again after a reload, unless it was already executed.
  useEffect(() => {
    const last = chat.messages[chat.messages.length - 1]
    if (last?.reply?.executable && last.executedRunId === undefined) setPending(last.reply)
  }, [])

  async function send(text: string) {
    setError('')
    setInfo('')
    setPending(null)
    const withUser: ChatState = { ...chat, messages: [...chat.messages, { role: 'user', text }] }
    setChat(withUser)
    setDraft('')
    setThinkingSince(Date.now())
    try {
      const reply = await api.chat(text, chat.sessionId)
      setChat({
        sessionId: reply.session_id ?? chat.sessionId,
        messages: [...withUser.messages, { role: 'agent', text: reply.message, reply }],
      })
      if (reply.executable) setPending(reply)
    } catch (e) {
      setError(`雲端沒有回應：${errText(e)}`)
      setDraft(text)
    } finally {
      setThinkingSince(null)
    }
  }

  // Retry from the feedback page: send the edited prompt in the same conversation, once.
  useEffect(() => {
    const st = location.state as TaskPageState | null
    if (st?.send && !handedOff.current) {
      handedOff.current = true
      navigate('.', { replace: true, state: null })
      void send(st.send)
    }
  }, [location.state])

  async function reset() {
    if (!confirm('開一個新的對話？目前的對話紀錄會清空。')) return
    try {
      const r = await api.resetSession(chat.sessionId)
      setChat({ sessionId: r.session_id, messages: [] })
    } catch (e) {
      setError(errText(e))
      return
    }
    setPending(null)
    setInfo('')
  }

  function lastUserPrompt(): string {
    return [...chat.messages].reverse().find((m) => m.role === 'user')?.text ?? ''
  }

  function markExecuted(missionId: string, runId: string | null) {
    setChat({
      ...chat,
      messages: chat.messages.map((m) => (m.reply?.mission_id === missionId ? { ...m, executedRunId: runId } : m)),
    })
  }

  async function execute() {
    if (!pending?.mission_id) return
    setExecuting(true)
    setError('')
    const prompt = lastUserPrompt() || pending.goal
    try {
      const baseline = (await latestStatus().catch(() => null))?.run_id ?? null
      const since = Date.now()
      const r = await api.execute(pending.mission_id, prompt)
      if (r.run_id) rememberPrompt(r.run_id, prompt)
      setTracking({ missionId: pending.mission_id, prompt, runId: r.run_id ?? undefined, baseline, since })
      markExecuted(pending.mission_id, r.run_id)
      setPending(null)
      setInfo(r.run_id ? '已開始執行。' : '已送出執行，正在等待機器人開始…')
    } catch (e) {
      setError(`無法執行：${errText(e)}`)
    } finally {
      setExecuting(false)
    }
  }

  const active = !!tracking || status?.state === 'running'

  usePolling(
    async () => {
      try {
        const s = tracking?.runId ? await api.btStatus(tracking.runId) : await latestStatus()
        if (tracking && !tracking.runId && s && s.run_id !== tracking.baseline && s.started_at * 1000 >= tracking.since - CLOCK_SLACK_MS) {
          rememberPrompt(s.run_id, tracking.prompt)
          setTracking({ ...tracking, runId: s.run_id })
        }
        setStatus(s)
        setStatusError('')
        if (s && tracking?.runId === s.run_id && s.state !== 'running') {
          setTracking(null)
          navigate(`/feedback/${encodeURIComponent(s.run_id)}`)
        }
      } catch (e) {
        setStatusError(errText(e))
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

  async function cancel() {
    if (!confirm('確定要中斷目前的任務？機器人會停下所有動作。')) return
    try {
      const r = tracking ? await api.cancelMission(tracking.missionId) : await api.btCancel()
      setInfo(r.was_running === false ? '目前沒有執行中的任務。' : '已送出中斷。')
    } catch (e) {
      setError(errText(e))
    }
  }

  const waiting = tracking && !tracking.runId

  return (
    <div className="page">
      <ChatPanel
        messages={chat.messages}
        thinkingSince={thinkingSince}
        onSend={send}
        onReset={reset}
        draft={draft}
        setDraft={setDraft}
      />

      {pending && <ConfirmCard reply={pending} busy={executing} onExecute={execute} onDismiss={() => setPending(null)} />}

      {(info || error) && (
        <div>
          {info && <p className="info">{info}</p>}
          {error && <p className="error">{error}</p>}
        </div>
      )}

      <section className="card">
        <div className="row between">
          <h2>任務狀態</h2>
          <button className="danger" onClick={cancel} disabled={status?.state !== 'running' && !waiting}>
            中斷任務
          </button>
        </div>
        {waiting && (
          <div className="waiting">
            <span className="spinner" />
            <div>
              <div>等待機器人開始執行…</div>
              <small className="muted">「{tracking.prompt}」</small>
            </div>
            <button className="link" onClick={() => setTracking(null)}>
              不等了
            </button>
          </div>
        )}
        {statusError && <p className="error">{statusError}</p>}
        {!waiting && !status && !statusError && <p className="muted">還沒有任何任務紀錄。</p>}
        {!waiting && status && (
          <>
            <div className="row status-line">
              <StatusBadge state={status.state} />
              <code>{status.run_id}</code>
              <span className="muted">{status.elapsed_s?.toFixed(1)} 秒</span>
            </div>
            {status.state === 'running' && (
              <p>
                目前動作：<b>{status.running_leaves.length ? status.running_leaves.join('、') : '—'}</b>
              </p>
            )}
            {status.state !== 'running' && (
              <Link to={`/feedback/${encodeURIComponent(status.run_id)}`}>查看結果與回饋 →</Link>
            )}
            <details open={status.state === 'running'}>
              <summary>執行紀錄</summary>
              <RunTimeline trace={status.trace ?? []} truncated={status.trace_truncated} />
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

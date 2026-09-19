import type { ChatReply } from '../api/types'

const ACTION_LABEL: Record<string, string> = {
  VisualizeObject: '用相機尋找物品',
  IsObjectFound: '確認是否找到',
  NavigateToDetectedObject: '移動到物品旁',
  NavigateToPoint: '移動到指定座標',
  TrackObject: '靠近物品',
  RotateInPlace: '原地旋轉',
  SetGripper: '控制夾爪',
  Patrol: '巡邏搜尋',
}

function describeArgs(args: Record<string, unknown>): string {
  return Object.entries(args)
    .map(([k, v]) => (k === 'position' ? `夾爪 ${v}/100` : `${k}=${String(v)}`))
    .join('、')
}

/** Shows the planned mission and asks the user before anything moves. */
export function ConfirmCard({
  reply,
  busy,
  onExecute,
  onDismiss,
}: {
  reply: ChatReply
  busy: boolean
  onExecute: () => void
  onDismiss: () => void
}) {
  return (
    <section className="card confirm">
      <h2>確認執行</h2>
      {reply.goal && <p>目標：<b>{reply.goal}</b></p>}
      {reply.steps.length > 0 ? (
        <ol className="plan">
          {reply.steps.map((s, i) => (
            <li key={i}>
              <b>{ACTION_LABEL[s.action] ?? s.action}</b>
              {s.objective && <span className="muted">：{s.objective}</span>}
              {Object.keys(s.arguments).length > 0 && <small className="muted"> （{describeArgs(s.arguments)}）</small>}
            </li>
          ))}
        </ol>
      ) : (
        <p className="muted">雲端沒有提供步驟摘要，可以展開下方查看行為樹。</p>
      )}
      {reply.bt_xml && (
        <details>
          <summary>查看行為樹 XML</summary>
          <pre className="log">{reply.bt_xml}</pre>
        </details>
      )}
      <p className="muted warn-text">按下執行後機器人會開始移動，請確認周圍安全。</p>
      <div className="row between">
        <button onClick={onDismiss} disabled={busy}>
          先不要，繼續修改
        </button>
        <button className="primary" onClick={onExecute} disabled={busy}>
          {busy ? '送出中…' : '確認執行'}
        </button>
      </div>
    </section>
  )
}

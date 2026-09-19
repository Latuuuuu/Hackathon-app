import { useState } from 'react'
import { api } from '../api/client'
import type { CalibState } from '../api/types'
import { usePolling } from '../lib/usePolling'

type Light = 'ok' | 'bad' | 'unknown' | 'mock'

function Dot({ light, label, title }: { light: Light; label: string; title: string }) {
  return (
    <span className={`dot dot-${light}`} title={title}>
      {label}
    </span>
  )
}

export function ConnectionBar() {
  const [bt, setBt] = useState<{ light: Light; msg: string }>({ light: 'unknown', msg: '檢查中' })
  const [calib, setCalib] = useState<CalibState | null>(null)

  usePolling(async () => {
    try {
      await api.btHealth()
      setBt({ light: 'ok', msg: 'BT engine 連線正常' })
    } catch (e) {
      setBt({ light: 'bad', msg: e instanceof Error ? e.message : String(e) })
    }
    try {
      setCalib(await api.calibState())
    } catch {
      setCalib(null)
    }
  }, 5000)

  const calibLight: Light = !calib || calib.error ? 'bad' : calib.mode === 'mock' ? 'mock' : calib.connected ? 'ok' : 'bad'
  const calibMsg = !calib
    ? '連不上 App Gateway'
    : calib.error
      ? `校正功能停用：${calib.error}`
      : calib.mode === 'mock'
      ? '校正模組為 mock 模式（沒有 ROS）'
      : calib.connected
        ? 'field_calib_node 已連線'
        : 'field_calib_node 未回應'

  return (
    <div className="conn">
      <Dot light={bt.light} label="BT" title={bt.msg} />
      <Dot light={calibLight} label="校正" title={calibMsg} />
    </div>
  )
}

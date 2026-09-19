// Shapes returned by the gateway. BT ones mirror bt_engine (mc_main_nav docs/llm_interface.md).

export type BtState = 'running' | 'success' | 'failure' | 'canceled' | 'error'

export interface TraceEvent {
  t: number
  node: string
  type: string
  from: string
  to: string
}

export interface BtNote {
  node: string
  message: string
}

export interface BtStatus {
  run_id: string
  state: BtState
  running_leaves: string[]
  elapsed_s: number
  notes: BtNote[]
  last_leaf_failure: TraceEvent | null
  trace: TraceEvent[]
  trace_truncated?: boolean
  started_at: number
  finished_at?: number
  error?: string
}

export interface RunSummary {
  run_id: string
  state: BtState
  started_at: number
}

export interface TaskRequest {
  prompt: string
  retry_of?: { run_id: string; notes: string[] }
}

export interface TaskResponse {
  ok: boolean
  task_id?: string
  run_id?: string
  mock?: boolean
  executed?: boolean
}

export type GripForce = 'too_weak' | 'ok' | 'too_strong'

export interface Feedback {
  run_id?: string
  outcome: BtState | 'unknown'
  grip_force?: GripForce
  rating?: number
  comment?: string
}

export interface FieldConfig {
  length: number
  depth: number
  count: number
  disabled_segments: string[]
}

export interface SegmentQuality {
  accepted: number
  samples: number
  rms_px: number
  mean_px: number
}

export interface CalibResult {
  calibrated_at: string
  table: { length: number; depth: number; count: number }
  cam_tf: { x: number; y: number; z: number; roll: number; pitch: number; yaw: number }
  table_dx?: number[]
  init?: string
  passed: boolean
  problems: string[]
  quality: {
    accept_ratio: number
    rms_px: number
    segments: Record<string, SegmentQuality>
    depth_plane?: Record<string, number>
  }
}

export interface CalibRunResponse {
  success: boolean
  message: string
  run: string | null
  result: CalibResult | null
}

export interface CalibState {
  mode: 'ros' | 'mock'
  error: string | null
  connected: boolean
  running_since: number | null
  latest_run: string | null
  streams: Record<'live' | 'calib', number | null>
}

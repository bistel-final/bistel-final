import apiClient, { USE_MOCK, mockResponse } from './client.js'
import { toIso, page } from './format.js'
import { ALARMS } from '../../features/detection/mock/alarms.js'
import { DASHBOARD } from '../../features/detection/mock/dashboard.js'
import { WAFER_TRACES, TRACE_LIMITS, TRACE_ANOMALY, MEASURED_STEP_STATS } from '../../features/detection/mock/trace.js'
import { ACTIONS } from '../../features/agent/mock/actions.js'
import { RUNS } from '../../features/agent/mock/runs.js'

// incident(LOT+챔버+파라미터) → 조치·런 역인덱스
const actionOfAlarm = {}
for (const a of ACTIONS) for (const id of a.alarm_ids) actionOfAlarm[id] = a
const runOfAlarm = {}
for (const r of RUNS) for (const id of r.alarm_ids) runOfAlarm[id] = r

const toItem = (a) => {
  const act = actionOfAlarm[a.alarm_id]
  const run = runOfAlarm[a.alarm_id]
  return {
    alarm_id: a.alarm_id,
    occurred_at: toIso(a.occurred_at),
    lot_id: a.lot_id,
    lot_hist_id: a.lot_hist_id,
    wafer_no: a.wafer_no,
    chamber_id: a.chamber_id,
    equipment_id: a.equipment_id,
    sensor_id: a.sensor_id,
    recipe_step_no: a.recipe_step_no,
    recipe_step_name: a.recipe_step_name,
    rule_id: a.rule_id,
    judgement: a.judgement,
    hit_cnt: a.hit_cnt,
    detail: a.detail,
    action_id: act?.action_id ?? null,
    action_code: act?.action_code ?? null,
    approval_status: act?.approval_status ?? null,
    incident: { lot_id: a.lot_id, chamber_id: a.chamber_id, sensor_id: a.sensor_id },
    latest_agent_run_id: run?.run_id ?? null,
    agent_run_status: run?.status ?? null,
  }
}

export function getDashboard(date, area) {
  if (USE_MOCK) return mockResponse(DASHBOARD)
  return apiClient.get('/dashboard/summary', { params: { date, area } }).then((r) => r.data)
}

// 목록 규격 {items, total, page, size} — mock은 전량 반환하고 필터·정렬은 화면에서 수행
export function getAlarms(filter = {}) {
  if (USE_MOCK) return mockResponse(page(ALARMS.map(toItem), filter))
  return apiClient.get('/alarms', { params: filter }).then((r) => r.data)
}

export function getAlarm(alarmId) {
  if (USE_MOCK) {
    const a = ALARMS.find((x) => x.alarm_id === alarmId)
    return mockResponse(a ? toItem(a) : null)
  }
  return apiClient.get(`/alarms/${alarmId}`).then((r) => r.data)
}

// 웨이퍼 단위 trace. 화면에서 웨이퍼별로 호출해 병합한다.
export function getTrace(lotHistId, sensor) {
  if (USE_MOCK) {
    const w = WAFER_TRACES.find((t) => t.lot_hist_id === lotHistId && (!sensor || t.sensor_id === sensor))
    return mockResponse(w ? { ...w, occurred_at: toIso(w.occurred_at), limits: TRACE_LIMITS } : null)
  }
  return apiClient.get(`/traces/${lotHistId}`, { params: { sensor } }).then((r) => r.data)
}

// 트레이스 뷰어 필터용 — 전체 웨이퍼 트레이스와 한계선·참고 지표
export function getTraceCatalog() {
  if (USE_MOCK) {
    return mockResponse({
      wafers: WAFER_TRACES.map((w) => ({ ...w, occurred_at: toIso(w.occurred_at) })),
      limits: TRACE_LIMITS,
      anomaly: TRACE_ANOMALY,
      measuredStepStats: MEASURED_STEP_STATS,
    })
  }
  return apiClient.get('/traces').then((r) => r.data)
}

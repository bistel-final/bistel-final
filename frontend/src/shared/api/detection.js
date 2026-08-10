import apiClient, { USE_MOCK, mockResponse } from './client.js'
import { toIso, page } from './format.js'
import { ALARMS } from '../../features/detection/mock/alarms.js'
import { DASHBOARD } from '../../features/detection/mock/dashboard.js'
import { WAFER_TRACES, TRACE_LIMITS, TRACE_ANOMALY, MEASURED_STEP_STATS } from '../../features/detection/mock/trace.js'
import { ACTIONS } from '../../features/agent/mock/actions.js'
import { RUNS } from '../../features/agent/mock/runs.js'

// 알람 → 조치·런 역인덱스. incident 키는 (lot_id, chamber_id) 이고
// 파라미터는 식별 키가 아니라 표시용 형제 필드다 (설계 4.1).
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
    incident: { lot_id: a.lot_id, chamber_id: a.chamber_id },
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

// 조회 선택지만 반환한다. 실제 시계열은 searchTraces() 가 준다 (설계 10.2).
// TODO(front): mock 은 화면 전환 없이 쓰도록 wafers 를 함께 넘긴다.
//   실제 API 로 바꿀 때 화면이 searchTraces() 를 호출하도록 옮긴다.
export function getTraceCatalog() {
  if (USE_MOCK) {
    return mockResponse({
      wafers: WAFER_TRACES.map((w) => ({ ...w, occurred_at: toIso(w.occurred_at) })),
      limits: TRACE_LIMITS,
      anomaly: TRACE_ANOMALY,
      measuredStepStats: MEASURED_STEP_STATS,
    })
  }
  return apiClient.get('/traces/catalog').then((r) => r.data)
}

// 파라미터 다중 · WAFER 다중 · 기간을 함께 받으므로 POST 를 쓴다 (설계 10.2).
// query string 에는 배열 표준 표기가 없어 JSON body 로 보낸다.
export function searchTraces(body = {}) {
  if (USE_MOCK) {
    const { sensor_ids, lot_id, wafer_nos, chamber_id, from, to } = body
    const hit = (w) =>
      (!sensor_ids?.length || sensor_ids.includes(w.sensor_id)) &&
      (!lot_id || w.lot_id === lot_id) &&
      (!wafer_nos?.length || wafer_nos.includes(w.wafer_no)) &&
      (!chamber_id || w.chamber_id === chamber_id) &&
      (!from || w.occurred_at >= from) &&
      (!to || w.occurred_at <= to)
    const wafers = WAFER_TRACES.filter(hit).map((w) => ({ ...w, occurred_at: toIso(w.occurred_at) }))
    return mockResponse({ wafers, limits: TRACE_LIMITS, total: wafers.length })
  }
  return apiClient.post('/traces/search', body).then((r) => r.data)
}

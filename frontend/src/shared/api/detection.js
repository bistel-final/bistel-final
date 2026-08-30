import apiClient, { mockEnabledFor, mockResponse } from './client.js'
import { assertExactObject, compactParams, requireDatePair, requireNonEmptyString } from './contract.js'
import { CORE_AGENT_RUN, CORE_ALARM, CORE_PARAMETER, CORE_TRACE_POINT } from './contractMocks.js'
import { toIso } from './format.js'
import { ALARMS } from '../../features/detection/mock/alarms.js'
import {
  DASHBOARD_BASE,
  DASHBOARD_ALARMS,
  AREA_OF_EQUIPMENT,
  ALL_SENSORS,
} from '../../features/detection/mock/dashboard.js'
import {
  WAFER_TRACES,
  TRACE_CATALOG,
  TRACE_SENSORS,
  MEASURED_STEP_STATS,
} from '../../features/detection/mock/trace.js'
import { ACTIONS } from '../../features/agent/mock/actions.js'
import { RUNS } from '../../features/agent/mock/runs.js'

// 백엔드 detection 라우터 구현 전까지 도메인 오버라이드로 mock 유지 가능 (client.js 참조)
const USE_MOCK = mockEnabledFor('DETECTION')

// incident(lot_id, chamber_id) → 조치·런 역인덱스
const actionOfAlarm = {}
for (const a of ACTIONS) for (const id of a.alarm_ids) actionOfAlarm[id] = a
const runOfAlarm = {}
for (const r of RUNS) for (const id of r.alarm_ids) runOfAlarm[id] = r

// AlarmItem — incident 는 (lot_id, chamber_id) 두 개뿐이다. sensor_id 등은 형제 필드.
const alarmSourceOf = (alarm) =>
  alarm.source ?? (String(alarm.rule_id).startsWith('R03') ? 'R03' : alarm.judgement === 'OOS' ? 'TRACE' : 'SUMMARY')

// Agent 화면의 canonical R03 근거는 배포 데이터 기반 legacy ALM-* mock과 ID 체계가
// 다르다. source-aware deep-link도 mock에서 실제 공개 응답과 같은 필드를 복원한다.
const CORE_R03_ALARM_DETAIL = Object.freeze({
  alarm_id: CORE_AGENT_RUN.alarm_id,
  source: CORE_AGENT_RUN.alarm_source,
  area: 'Etch',
  lot_hist_id: 'LH-000004',
  lot_id: 'LOT004',
  wafer_no: 2,
  chamber_id: CORE_AGENT_RUN.chamber_id,
  equipment_id: 'EQP04',
  sensor_id: 'ET_REFL',
  recipe_step_no: 1,
  recipe_step_name: 'MAIN_ETCH',
  rule_id: 'R03_CONSEC',
  judgement: 'OOS',
  hit_cnt: 3,
  detail: 'OOS for 3 consecutive WAFER at MAIN_ETCH',
  occurred_at: CORE_AGENT_RUN.created_at,
  incident: { lot_id: 'LOT004', chamber_id: CORE_AGENT_RUN.chamber_id },
  action_id: CORE_AGENT_RUN.action_id,
  action_code: CORE_AGENT_RUN.recommended_action,
  approval_status: 'PENDING',
  latest_agent_run_id: CORE_AGENT_RUN.agent_run_id,
  agent_run_status: CORE_AGENT_RUN.status,
})

const toItem = (a) => {
  const act = actionOfAlarm[a.alarm_id]
  const run = runOfAlarm[a.alarm_id]
  return {
    alarm_id: a.alarm_id,
    source: alarmSourceOf(a),
    lot_hist_id: a.lot_hist_id,
    lot_id: a.lot_id,
    wafer_no: Number(a.wafer_no),
    chamber_id: a.chamber_id,
    equipment_id: a.equipment_id,
    sensor_id: a.sensor_id,
    recipe_step_no: Number(a.recipe_step_no),
    recipe_step_name: a.recipe_step_name,
    rule_id: a.rule_id,
    judgement: a.judgement,
    hit_cnt: Number(a.hit_cnt),
    detail: a.detail,
    occurred_at: toIso(a.occurred_at),
    incident: { lot_id: a.lot_id, chamber_id: a.chamber_id },
    action_id: act?.action_id ?? null,
    action_code: act?.action_code ?? null,
    approval_status: act?.approval_status ?? null,
    latest_agent_run_id: run?.agent_run_id ?? null,
    agent_run_status: run?.status ?? null,
  }
}

const areaOf = (equipmentId) => AREA_OF_EQUIPMENT[equipmentId] ?? null

const matchScope = (a, { area, equipment_id, chamber_id, sensor_id, judgement, date }) =>
  (!area || areaOf(a.equipment_id) === area) &&
  (!equipment_id || a.equipment_id === equipment_id) &&
  (!chamber_id || a.chamber_id === chamber_id) &&
  (!sensor_id || a.sensor_id === sensor_id) &&
  (!judgement || a.judgement === judgement) &&
  (!date || String(a.occurred_at).startsWith(date))

// GET /dashboard/summary — date?·area?·equipment_id?·chamber_id?
export function getDashboard(params = {}) {
  if (USE_MOCK) {
    const rows = DASHBOARD_ALARMS.filter((a) => matchScope(a, params))
    const count = (list, j) => list.filter((a) => a.judgement === j).length
    const bySensor = {}
    const byEquipment = {}
    for (const a of rows) {
      bySensor[a.sensor_id] = (bySensor[a.sensor_id] ?? 0) + 1
      byEquipment[a.equipment_id] = (byEquipment[a.equipment_id] ?? 0) + 1
    }
    return mockResponse({
      reference_date: DASHBOARD_BASE.reference_date,
      area: params.area ?? null,
      hierarchy: DASHBOARD_BASE.hierarchy,
      sensor_catalog: ALL_SENSORS,
      alarm_count: rows.length,
      oos_count: count(rows, 'OOS'),
      ooc_count: count(rows, 'OOC'),
      daily_trend: DASHBOARD_BASE.date_range.map((date) => {
        const day = rows.filter((a) => a.occurred_at.startsWith(date))
        return {
          date,
          oos_count: count(day, 'OOS'),
          ooc_count: count(day, 'OOC'),
          has_r03_consec: day.some((a) => a.rule_id === 'R03_CONSEC'),
        }
      }),
      top_sensors: Object.entries(bySensor)
        .map(([sensor_id, alarm_count]) => ({
          sensor_id,
          alarm_count,
          chamber_ids: [...new Set(rows.filter((a) => a.sensor_id === sensor_id).map((a) => a.chamber_id))].sort(),
        }))
        .sort((x, y) => y.alarm_count - x.alarm_count || x.sensor_id.localeCompare(y.sensor_id)),
      equipment_counts: DASHBOARD_BASE.hierarchy
        .filter(
          (e) =>
            (!params.area || e.area_id === params.area) &&
            (!params.equipment_id || e.equipment_id === params.equipment_id),
        )
        .map((e) => ({
          equipment_id: e.equipment_id,
          area_id: e.area_id,
          alarm_count: byEquipment[e.equipment_id] ?? 0,
          chambers: e.chambers
            .filter((c) => !params.chamber_id || c === params.chamber_id)
            .map((chamber_id) => ({
              chamber_id,
              alarm_count: rows.filter((a) => a.chamber_id === chamber_id).length,
            }))
            .sort((x, y) => y.alarm_count - x.alarm_count || x.chamber_id.localeCompare(y.chamber_id)),
        }))
        .sort((x, y) => y.alarm_count - x.alarm_count || x.equipment_id.localeCompare(y.equipment_id)),
      pending_approvals: DASHBOARD_BASE.pending_approvals.map((p) => ({
        ...p,
        requested_at: toIso(p.requested_at),
      })),
      // AlarmItem — 조치 링크가 필요하므로 action_id·action_code 를 함께 실어 준다
      recent_alarms: [...rows]
        .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at) || b.alarm_id.localeCompare(a.alarm_id))
        .slice(0, 6)
        .map((a) => ({
          ...a,
          occurred_at: toIso(a.occurred_at),
          action_id: actionOfAlarm[a.alarm_id]?.action_id ?? null,
          action_code: actionOfAlarm[a.alarm_id]?.action_code ?? null,
        })),
    })
  }
  return apiClient.get('/dashboard/summary', { params }).then((r) => r.data)
}

// GET /alarms — date?·area?·equipment_id?·chamber_id?·sensor_id?·judgement?·page·size
export function getAlarms(params = {}) {
  if (USE_MOCK) {
    const { page: p = 1, size = 20, ...scope } = params
    const rows = ALARMS.filter((a) => matchScope(a, scope))
      .map(toItem)
      .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at) || b.alarm_id.localeCompare(a.alarm_id))
    return mockResponse({ items: rows.slice((p - 1) * size, p * size), total: rows.length, page: p, size })
  }
  return apiClient.get('/alarms/paged', { params }).then((r) => r.data)
}

// GET /alarms — core compatibility contract. The existing getAlarms() is the
// deprecated paged UI adapter and intentionally uses /alarms/paged.
export function getAlarmsCore(params = {}) {
  assertExactObject(
    params,
    ['area', 'equipment', 'chamber', 'parameter', 'source', 'include_derived', 'date_from', 'date_to'],
    'getAlarmsCore params',
  )
  requireDatePair(params, 'getAlarmsCore params')
  const query = compactParams(params)
  if (USE_MOCK) return mockResponse([CORE_ALARM])
  return apiClient.get('/alarms', { params: query }).then((response) => response.data)
}

// GET /trace — the four identifiers are the complete public query contract.
export function getTrace(params) {
  assertExactObject(params, ['lot', 'wafer', 'chamber', 'parameter'], 'getTrace params')
  const query = Object.fromEntries(
    ['lot', 'wafer', 'chamber', 'parameter'].map((key) => [key, requireNonEmptyString(params[key], `getTrace.${key}`)]),
  )
  if (USE_MOCK) return mockResponse([CORE_TRACE_POINT])
  return apiClient.get('/trace', { params: query }).then((response) => response.data)
}

// GET /parameters — core reference-data contract, distinct from /traces/catalog.
export function getParameters() {
  if (USE_MOCK) return mockResponse([CORE_PARAMETER])
  return apiClient.get('/parameters').then((response) => response.data)
}

export function getAlarm(alarmId, source = null) {
  if (USE_MOCK) {
    if (
      alarmId === CORE_R03_ALARM_DETAIL.alarm_id &&
      (!source || source === CORE_R03_ALARM_DETAIL.source)
    ) {
      return mockResponse(CORE_R03_ALARM_DETAIL)
    }
    const a = ALARMS.find(
      (item) =>
        item.alarm_id === alarmId &&
        (!source || alarmSourceOf(item) === source),
    )
    return mockResponse(a ? toItem(a) : null)
  }
  return getAlarmsCore(source ? { source } : {}).then(
    (alarms) => alarms.find(
      (alarm) => alarm.alarm_id === alarmId && (!source || alarm.source === source),
    ) ?? null,
  )
}

// GET /traces/catalog — 조회 선택지 + 센서별 한계선·단위
export function getTraceCatalog() {
  if (USE_MOCK) return mockResponse(TRACE_CATALOG)
  return apiClient.get('/traces/catalog').then((r) => r.data)
}

// POST /traces/search — 다중 웨이퍼·다중 파라미터를 한 번에 조회한다 (웨이퍼별 개별 호출 금지)
export function searchTraces(body = {}) {
  if (USE_MOCK) {
    const { area, equipment_id, chamber_id, sensor_ids, recipe_id, lot_id, wafer_nos, from, to } = body
    const wafers = WAFER_TRACES.filter(
      (w) =>
        (!area || areaOf(w.equipment_id) === area) &&
        (!equipment_id || w.equipment_id === equipment_id) &&
        (!chamber_id || w.chamber_id === chamber_id) &&
        (!sensor_ids?.length || sensor_ids.includes(w.sensor_id)) &&
        (!recipe_id || w.recipe_id === recipe_id) &&
        (!lot_id || w.lot_id === lot_id) &&
        (!wafer_nos?.length || wafer_nos.map(Number).includes(w.wafer_no)) &&
        (!from || w.occurred_at >= from) &&
        (!to || w.occurred_at <= to),
    ).map((w) => ({ ...w, occurred_at: toIso(w.occurred_at), points: w.points.map((p) => ({ ...p, measured_at: toIso(p.measured_at) })) }))
    // 한계선은 센서별로 다르다 — sensor_id 로 키를 잡아 내려준다
    const limits = Object.fromEntries(
      [...new Set(wafers.map((w) => w.sensor_id))].map((id) => [id, TRACE_SENSORS.find((s) => s.sensor_id === id) ?? null]),
    )
    return mockResponse({ wafers, limits, total: wafers.length, measured_step_stats: MEASURED_STEP_STATS })
  }
  return apiClient.post('/traces/search', body).then((response) => response.data)
}

export { MEASURED_STEP_STATS }

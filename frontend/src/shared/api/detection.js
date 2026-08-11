import apiClient, { USE_MOCK, mockResponse } from './client.js'
<<<<<<< Updated upstream
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
  TRACE_ANOMALY,
  MEASURED_STEP_STATS,
} from '../../features/detection/mock/trace.js'
import { ACTIONS } from '../../features/agent/mock/actions.js'
import { RUNS } from '../../features/agent/mock/runs.js'

// incident(lot_id, chamber_id) → 조치·런 역인덱스
const actionOfAlarm = {}
for (const a of ACTIONS) for (const id of a.alarm_ids) actionOfAlarm[id] = a
const runOfAlarm = {}
for (const r of RUNS) for (const id of r.alarm_ids) runOfAlarm[id] = r

// AlarmItem — incident 는 (lot_id, chamber_id) 두 개뿐이다. sensor_id 등은 형제 필드.
const toItem = (a) => {
  const act = actionOfAlarm[a.alarm_id]
  const run = runOfAlarm[a.alarm_id]
  return {
    alarm_id: a.alarm_id,
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
=======
import { page, toIso } from './format.js'
import { ACTIONS, APPROVALS } from '../../features/agent/mock/actions.js'
import { RUNS } from '../../features/agent/mock/runs.js'
import { ALARMS } from '../../features/detection/mock/alarms.js'
import { DASHBOARD } from '../../features/detection/mock/dashboard.js'
import {
  MEASURED_STEP_STATS,
  TRACE_ANOMALY,
  TRACE_LIMITS,
  WAFER_TRACES,
} from '../../features/detection/mock/trace.js'

const AREA_BY_PREFIX = { PHO: 'PHOTO', ETC: 'ETCH' }
const SENSOR_CATALOG = ['PH_FOCUS', 'PH_DOSE', 'PH_PEB', 'PH_DEV', 'ET_REFL', 'ET_CF4', 'ET_PRES', 'ET_ESC']
const areaOf = (item) => AREA_BY_PREFIX[String(item.equipment_id ?? item.chamber_id ?? '').slice(0, 3)] ?? null
const equipmentOf = (chamberId) => String(chamberId ?? '').replace(/-C\d+$/, '')
const dateOf = (value) => String(value ?? '').slice(0, 10)

const actionByAlarm = {}
for (const action of ACTIONS) {
  for (const alarmId of action.alarm_ids ?? []) actionByAlarm[alarmId] = action
}
const runByAlarm = {}
for (const run of RUNS) {
  for (const alarmId of run.alarm_ids ?? []) runByAlarm[alarmId] = run
}

const toAlarmItem = (alarm) => {
  const action = actionByAlarm[alarm.alarm_id]
  const run = runByAlarm[alarm.alarm_id]
  return {
    alarm_id: alarm.alarm_id,
    lot_hist_id: alarm.lot_hist_id,
    lot_id: alarm.lot_id,
    wafer_no: alarm.wafer_no == null ? null : Number(alarm.wafer_no),
    chamber_id: alarm.chamber_id ?? null,
    equipment_id: alarm.equipment_id ?? null,
    sensor_id: alarm.sensor_id ?? null,
    recipe_step_no: alarm.recipe_step_no == null ? null : Number(alarm.recipe_step_no),
    recipe_step_name: alarm.recipe_step_name ?? null,
    rule_id: alarm.rule_id ?? null,
    judgement: alarm.judgement ?? null,
    hit_cnt: alarm.hit_cnt == null ? null : Number(alarm.hit_cnt),
    detail: alarm.detail ?? null,
    occurred_at: toIso(alarm.occurred_at),
    incident: { lot_id: alarm.lot_id, chamber_id: alarm.chamber_id },
    action_id: action?.action_id ?? null,
    action_code: action?.action_code ?? null,
    approval_status: action?.approval_status ?? null,
>>>>>>> Stashed changes
    latest_agent_run_id: run?.agent_run_id ?? null,
    agent_run_status: run?.status ?? null,
  }
}

<<<<<<< Updated upstream
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
  return apiClient.get('/alarms', { params }).then((r) => r.data)
=======
const alarmItems = ALARMS.map(toAlarmItem).sort(
  (a, b) => b.occurred_at.localeCompare(a.occurred_at) || b.alarm_id.localeCompare(a.alarm_id),
)

const hierarchy = DASHBOARD.hierarchy.flatMap((area) =>
  area.equipments.map((equipment) => ({
    area_id: area.area,
    equipment_id: equipment.id,
    model_code: equipment.model ?? null,
    chambers: [...equipment.chambers],
  })),
)

const canonicalPendingApproval = (approval) => {
  const action = ACTIONS.find((item) => item.action_id === approval.action_id)
  return {
    approval_id: approval.approval_id,
    agent_run_id: approval.agent_run_id,
    action_id: approval.action_id,
    incident: { lot_id: action.incident.lot_id, chamber_id: action.incident.chamber_id },
    equipment_id: equipmentOf(action.incident.chamber_id),
    sensor_id: action.incident.sensor_id ?? null,
    rule_id: action.action_code === 'EQP_HOLD' ? 'R03_CONSEC' : null,
    action_code: action.action_code,
    severity: action.severity,
    requested_at: toIso(approval.requested_at),
    status: approval.status,
    decided_by: approval.decided_by ?? null,
    decided_at: toIso(approval.decided_at),
    decision_comment: approval.decision_comment ?? null,
  }
}

function mockDashboardSummary({ date_from, date_to, area, equipment_id, chamber_id } = {}) {
  const scoped = alarmItems.filter(
    (alarm) =>
      (!area || areaOf(alarm) === area) &&
      (!equipment_id || alarm.equipment_id === equipment_id) &&
      (!chamber_id || alarm.chamber_id === chamber_id),
  )
  const dates = [...new Set(scoped.map((alarm) => dateOf(alarm.occurred_at)))].sort()
  const rangeStart = date_from || dates[0] || null
  const rangeEnd = date_to || dates.at(-1) || null
  const current = scoped.filter(
    (alarm) =>
      (!rangeStart || dateOf(alarm.occurred_at) >= rangeStart) &&
      (!rangeEnd || dateOf(alarm.occurred_at) <= rangeEnd),
  )
  const periodDates = dates.filter((day) => (!rangeStart || day >= rangeStart) && (!rangeEnd || day <= rangeEnd))
  const referenceDate = periodDates.at(-1) ?? rangeEnd

  const dailyTrend = periodDates.map((day) => {
    const items = current.filter((alarm) => dateOf(alarm.occurred_at) === day)
    return {
      date: day,
      oos_count: items.filter((alarm) => alarm.judgement === 'OOS').length,
      ooc_count: items.filter((alarm) => alarm.judgement === 'OOC').length,
      has_r03_consec: items.some((alarm) => alarm.rule_id === 'R03_CONSEC'),
    }
  })

  const bySensor = new Map()
  for (const alarm of current) {
    const entry = bySensor.get(alarm.sensor_id) ?? { sensor_id: alarm.sensor_id, alarm_count: 0, chambers: new Set() }
    entry.alarm_count += 1
    entry.chambers.add(alarm.chamber_id)
    bySensor.set(alarm.sensor_id, entry)
  }
  const topSensors = [...bySensor.values()]
    .map((item) => ({ sensor_id: item.sensor_id, alarm_count: item.alarm_count, chamber_ids: [...item.chambers].sort() }))
    .sort((a, b) => b.alarm_count - a.alarm_count || a.sensor_id.localeCompare(b.sensor_id))
    .slice(0, 5)

  const equipmentCounts = hierarchy
    .filter((node) => !area || node.area_id === area)
    .filter((node) => !equipment_id || node.equipment_id === equipment_id)
    .map((node) => {
      const items = current.filter((alarm) => alarm.equipment_id === node.equipment_id)
      return {
        equipment_id: node.equipment_id,
        area_id: node.area_id,
        alarm_count: items.length,
        chambers: node.chambers
          .filter((id) => !chamber_id || id === chamber_id)
          .map((id) => ({ chamber_id: id, alarm_count: items.filter((alarm) => alarm.chamber_id === id).length })),
      }
    })
    .sort((a, b) => b.alarm_count - a.alarm_count || a.equipment_id.localeCompare(b.equipment_id))

  const pendingApprovals = APPROVALS.filter((approval) => approval.status === 'PENDING')
    .map(canonicalPendingApproval)
    .sort((a, b) => b.requested_at.localeCompare(a.requested_at) || b.approval_id.localeCompare(a.approval_id))

  return {
    reference_date: referenceDate,
    area: area ?? null,
    date_range: rangeStart && rangeEnd ? [rangeStart, rangeEnd] : [],
    hierarchy,
    sensor_catalog: [...SENSOR_CATALOG],
    alarm_count: current.length,
    oos_count: current.filter((alarm) => alarm.judgement === 'OOS').length,
    ooc_count: current.filter((alarm) => alarm.judgement === 'OOC').length,
    daily_trend: dailyTrend,
    top_sensors: topSensors,
    equipment_counts: equipmentCounts,
    pending_approvals: pendingApprovals,
    recent_alarms: [...current].slice(0, 5),
  }
}

export function getDashboard(filter = {}) {
  if (USE_MOCK) return mockResponse(mockDashboardSummary(filter))
  return apiClient.get('/dashboard/summary', { params: filter }).then((response) => response.data)
}

export function getAlarms(filter = {}) {
  if (USE_MOCK) {
    let items = alarmItems
    const keyMap = { area: areaOf, equipment_id: (item) => item.equipment_id, chamber_id: (item) => item.chamber_id }
    for (const [key, getter] of Object.entries(keyMap)) {
      if (filter[key]) items = items.filter((item) => getter(item) === filter[key])
    }
    for (const key of ['sensor_id', 'rule_id', 'judgement']) {
      if (filter[key]) items = items.filter((item) => item[key] === filter[key])
    }
    if (filter.date_from) items = items.filter((item) => dateOf(item.occurred_at) >= filter.date_from)
    if (filter.date_to) items = items.filter((item) => dateOf(item.occurred_at) <= filter.date_to)
    return mockResponse(page(items, filter))
  }
  return apiClient.get('/alarms', { params: filter }).then((response) => response.data)
>>>>>>> Stashed changes
}

export function getAlarm(alarmId) {
  if (USE_MOCK) return mockResponse(alarmItems.find((item) => item.alarm_id === alarmId) ?? null)
  return apiClient.get(`/alarms/${alarmId}`).then((response) => response.data)
}

const canonicalLimits = {
  unit: 'nm',
  spec_lower: TRACE_LIMITS.find((limit) => limit.label === 'LSL')?.value ?? null,
  ctrl_lower: TRACE_LIMITS.find((limit) => limit.label === 'LCL')?.value ?? null,
  target: TRACE_LIMITS.find((limit) => limit.label === 'TARGET')?.value ?? null,
  ctrl_upper: TRACE_LIMITS.find((limit) => limit.label === 'UCL')?.value ?? null,
  spec_upper: TRACE_LIMITS.find((limit) => limit.label === 'USL')?.value ?? null,
  upper_only: false,
}

<<<<<<< Updated upstream
// GET /traces/catalog — 조회 선택지 + 센서별 한계선·단위
export function getTraceCatalog() {
  if (USE_MOCK) return mockResponse({ ...TRACE_CATALOG, anomaly: TRACE_ANOMALY })
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
=======
export function getTraceCatalog() {
  if (USE_MOCK) {
    const measuredSensors = new Set(WAFER_TRACES.map((trace) => trace.sensor_id))
    const lots = [...new Set(WAFER_TRACES.map((trace) => trace.lot_id))].sort().map((lotId) => ({
      lot_id: lotId,
      wafer_nos: [...new Set(WAFER_TRACES.filter((trace) => trace.lot_id === lotId).map((trace) => Number(trace.wafer_no)))].sort(
        (a, b) => a - b,
      ),
    }))
    return mockResponse({
      areas: [...new Set(hierarchy.map((node) => node.area_id))].sort().map((areaId) => ({ area_id: areaId })),
      equipments: hierarchy.map((node) => ({ ...node })),
      sensors: SENSOR_CATALOG.map((sensorId) => ({
        sensor_id: sensorId,
        sensor_name: sensorId,
        ...(measuredSensors.has(sensorId)
          ? canonicalLimits
          : {
              unit: null,
              spec_lower: null,
              ctrl_lower: null,
              target: null,
              ctrl_upper: null,
              spec_upper: null,
              upper_only: false,
            }),
      })),
      recipes: [],
      lots,
      anomaly: { threshold: TRACE_ANOMALY.threshold },
    })
  }
  return apiClient.get('/traces/catalog').then((response) => response.data)
}

const STEP_ORDER = { EXPOSE: 1, DEVELOP: 2, MAIN_ETCH: 1 }

const toTraceSeries = (trace) => {
  let seq = 1
  const points = []
  for (const [stepName, step] of Object.entries(trace.steps ?? {})) {
    for (const value of step.points ?? []) {
      points.push({
        seq_no: seq,
        recipe_step_no: STEP_ORDER[stepName] ?? null,
        recipe_step_name: stepName,
        measured_at: null,
        value,
      })
      seq += 1
    }
  }
  return {
    lot_hist_id: trace.lot_hist_id,
    lot_id: trace.lot_id,
    wafer_no: Number(trace.wafer_no),
    chamber_id: trace.chamber_id,
    equipment_id: equipmentOf(trace.chamber_id),
    recipe_id: null,
    sensor_id: trace.sensor_id,
    occurred_at: toIso(trace.occurred_at),
    points,
  }
}

function measuredStatsFor(traces) {
  const rows = []
  for (const trace of traces) {
    for (const [stepName, step] of Object.entries(trace.steps ?? {})) {
      const values = step.points ?? []
      if (!values.length && step.mean == null) continue
      const mean = values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : step.mean
      const variance =
        values.length > 1
          ? values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / (values.length - 1)
          : null
      rows.push({
        lot_hist_id: trace.lot_hist_id,
        sensor_id: trace.sensor_id,
        recipe_step_no: STEP_ORDER[stepName] ?? 1,
        recipe_step_name: stepName,
        value_mean: mean,
        value_std: variance == null ? null : Math.sqrt(variance),
        value_min: values.length ? Math.min(...values) : null,
        value_max: values.length ? Math.max(...values) : null,
        point_cnt: values.length,
        ooc_point_cnt: 0,
        oos_point_cnt: 0,
        judgement: 'IN_CONTROL',
      })
    }
  }
  return rows
}

export function searchTraces(body = {}) {
  if (USE_MOCK) {
    const { area, equipment_id, chamber_id, sensor_ids, recipe_id, lot_id, wafer_nos, from, to } = body
    const traces = WAFER_TRACES.filter(
      (trace) =>
        (!area || areaOf(trace) === area) &&
        (!equipment_id || equipmentOf(trace.chamber_id) === equipment_id) &&
        (!chamber_id || trace.chamber_id === chamber_id) &&
        (!sensor_ids?.length || sensor_ids.includes(trace.sensor_id)) &&
        (!recipe_id || false) &&
        (!lot_id || trace.lot_id === lot_id) &&
        (!wafer_nos?.length || wafer_nos.map(Number).includes(Number(trace.wafer_no))) &&
        (!from || dateOf(trace.occurred_at) >= dateOf(from)) &&
        (!to || dateOf(trace.occurred_at) <= dateOf(to)),
    )
    const selectedSensors = sensor_ids?.length ? sensor_ids : [...new Set(traces.map((trace) => trace.sensor_id))]
    return mockResponse({
      wafers: traces.map(toTraceSeries),
      limits: Object.fromEntries(
        selectedSensors.map((sensorId) => [sensorId, { ...canonicalLimits, upper_only: sensorId === 'ET_REFL' }]),
      ),
      measured_step_stats: measuredStatsFor(traces),
      total: traces.length,
    })
>>>>>>> Stashed changes
  }
  return apiClient.post('/traces/search', body).then((response) => response.data)
}

export { MEASURED_STEP_STATS }

import apiClient, { USE_MOCK, mockResponse } from './client.js'
<<<<<<< Updated upstream
import { toIso } from './format.js'
import { RUNS } from '../../features/agent/mock/runs.js'
import { ACTIONS, APPROVALS } from '../../features/agent/mock/actions.js'

const isoRun = (r) => ({
  ...r,
  incident_first_at: toIso(r.incident_first_at),
  incident_last_at: toIso(r.incident_last_at),
  started_at: toIso(r.started_at),
  ended_at: toIso(r.ended_at),
  tool_calls: r.tool_calls.map((t) => ({ ...t, called_at: toIso(t.called_at) })),
})

const isoAction = (a) => ({
  ...a,
  created_at: toIso(a.created_at),
  approved_at: toIso(a.approved_at),
  sent_at: toIso(a.sent_at),
})

const isoApproval = (p) => ({
  ...p,
  requested_at: toIso(p.requested_at),
  decided_at: toIso(p.decided_at),
})

const paginate = (rows, { page: p = 1, size = 20 } = {}) => ({
  items: rows.slice((p - 1) * size, p * size),
  total: rows.length,
  page: p,
  size,
})

// GET /agent/runs — status?·equipment_id?·chamber_id?·date_from?·date_to?·page·size
export function getRuns(params = {}) {
  if (USE_MOCK) {
    const { status, equipment_id, chamber_id, date_from, date_to, ...pageParams } = params
    const rows = RUNS.filter(
      (r) =>
        (!status || r.status === status) &&
        (!equipment_id || r.equipment_id === equipment_id) &&
        (!chamber_id || r.incident.chamber_id === chamber_id) &&
        (!date_from || r.started_at >= date_from) &&
        (!date_to || r.started_at <= date_to),
    )
      .map(isoRun)
      .sort((a, b) => b.started_at.localeCompare(a.started_at) || b.agent_run_id.localeCompare(a.agent_run_id))
    return mockResponse(paginate(rows, pageParams))
  }
  return apiClient.get('/agent/runs', { params }).then((r) => r.data)
}

export function getRun(agentRunId) {
  if (USE_MOCK) {
    const r = RUNS.find((x) => x.agent_run_id === agentRunId)
    return mockResponse(r ? isoRun(r) : null)
  }
  return apiClient.get(`/agent/runs/${agentRunId}`).then((r) => r.data)
}

// GET /approvals — status?·page·size
export function getApprovals(params = {}) {
  if (USE_MOCK) {
    const { status, ...pageParams } = params
    const rows = APPROVALS.filter((p) => !status || p.status === status)
      .map(isoApproval)
      .sort((a, b) => b.requested_at.localeCompare(a.requested_at) || b.approval_id.localeCompare(a.approval_id))
    return mockResponse(paginate(rows, pageParams))
  }
  return apiClient.get('/approvals', { params }).then((r) => r.data)
=======
import { page, toIso } from './format.js'
import { ACTIONS, APPROVALS, FAULT_BY_SENSOR } from '../../features/agent/mock/actions.js'
import { RUNS, RUN_TOOL_CALLS } from '../../features/agent/mock/runs.js'

const TERMINAL_RUN_STATUSES = new Set(['COMPLETED', 'FAILED'])

// fixture의 incident 안에 있던 표시 필드를 canonical 응답의 형제 필드로 올린다.
const flatIncident = ({ lot_id, chamber_id, sensor_id, recipe_step_name, first_at, last_at, alarm_count }) => ({
  incident: { lot_id, chamber_id },
  sensor_id,
  recipe_step_name,
  incident_first_at: toIso(first_at),
  incident_last_at: toIso(last_at),
  alarm_count,
})

const canonicalToolCall = (tool, index) => ({
  tool_call_id: tool.tool_call_id ?? `TOOL-MOCK-${String(index + 1).padStart(4, '0')}`,
  call_seq: tool.call_seq ?? tool.seq ?? index + 1,
  tool_name: tool.tool_name ?? tool.name,
  input: tool.input ?? null,
  output: tool.output ?? null,
  status: tool.status === 'OK' ? 'SUCCESS' : tool.status,
  latency_ms: tool.latency_ms ?? tool.ms ?? null,
  called_at: tool.called_at ?? '2026-06-04T07:10:41+09:00',
  error_msg: tool.error_msg ?? null,
})

const canonicalActionItem = (action) => {
  if (!action) return null
  const { alarm_ids = [], channel, agent_run_id, created_by_agent_run_id, incident } = action
  return {
    action_id: action.action_id,
    created_by_agent_run_id: created_by_agent_run_id ?? agent_run_id ?? null,
    incident: { lot_id: incident.lot_id, chamber_id: incident.chamber_id },
    equipment_id: incident.chamber_id.replace(/-C\d+$/, ''),
    sensor_id: incident.sensor_id ?? null,
    recipe_step_name: incident.recipe_step_name ?? null,
    action_code: action.action_code,
    severity: action.severity ?? null,
    approval_status: action.approval_status ?? null,
    send_status: action.send_status ?? null,
    send_channel: action.send_channel ?? channel ?? null,
    alarm_count: action.alarm_count ?? alarm_ids.length,
    created_at: toIso(action.created_at),
  }
}

const canonicalActionDetail = (action) => {
  const item = canonicalActionItem(action)
  if (!item) return null
  return {
    ...item,
    trigger_alarm_lot_hist_id: action.trigger_alarm_lot_hist_id ?? null,
    reason: action.reason ?? null,
    approval_required: action.action_code === 'EQP_HOLD',
    approved_by: action.approved_by ?? null,
    approved_at: toIso(action.approved_at),
    send_started_at: toIso(action.send_started_at),
    send_attempt_count: action.send_attempt_count ?? (action.send_status === 'SENT' ? 1 : 0),
    sent_at: toIso(action.sent_at),
    delivery: action.delivery ?? null,
  }
}

const actionById = Object.fromEntries(ACTIONS.map((action) => [action.action_id, action]))
const approvalByAction = Object.fromEntries(APPROVALS.map((approval) => [approval.action_id, approval]))

const canonicalApproval = (approval) => {
  if (!approval) return null
  const action = actionById[approval.action_id]
  return {
    approval_id: approval.approval_id,
    agent_run_id: approval.agent_run_id,
    action_id: approval.action_id ?? null,
    incident: { lot_id: action.incident.lot_id, chamber_id: action.incident.chamber_id },
    equipment_id: action.incident.chamber_id.replace(/-C\d+$/, ''),
    sensor_id: action.incident.sensor_id ?? null,
    rule_id: action.rule_id ?? null,
    action_code: action.action_code,
    severity: action.severity,
    requested_at: toIso(approval.requested_at),
    status: approval.status,
    decided_by: approval.decided_by ?? null,
    decided_at: toIso(approval.decided_at),
    decision_comment: approval.decision_comment ?? null,
  }
}

const canonicalRunItem = (run) => {
  const action = actionById[run.action_id]
  const incident = flatIncident(run.incident)
  return {
    agent_run_id: run.agent_run_id,
    ...incident,
    equipment_id: run.incident.chamber_id.replace(/-C\d+$/, ''),
    started_at: toIso(run.started_at ?? run.incident.first_at),
    ended_at: TERMINAL_RUN_STATUSES.has(run.status) ? toIso(run.ended_at ?? run.incident.last_at) : null,
    status: run.status,
    fault_code: run.fault_code ?? null,
    recommended_action: action?.action_code ?? null,
    severity: action?.severity ?? null,
  }
}

const canonicalRunDetail = (run) => {
  if (!run) return null
  const item = canonicalRunItem(run)
  const action = canonicalActionDetail(actionById[run.action_id])
  const approval = canonicalApproval(approvalByAction[run.action_id])
  const fault = FAULT_BY_SENSOR[item.sensor_id]
  const representativeAlarmId = run.representative_alarm_id ?? run.trigger_alarm_id ?? run.alarm_ids[0]

  return {
    ...item,
    thread_id: run.thread_id,
    requested_alarm_id: run.requested_alarm_id ?? representativeAlarmId,
    representative_alarm_id: representativeAlarmId,
    alarm_ids: [...run.alarm_ids],
    confidence: run.confidence ?? null,
    cause_summary: run.cause_summary ?? fault?.cause ?? null,
    action_reason: run.action_reason ?? null,
    approval_required: action?.approval_required ?? false,
    evidence: run.evidence ?? {
      representative_fdc: null,
      r03_fdc: null,
      incident: {
        alarm_ids: [...run.alarm_ids],
        lot_hist_ids: [],
        rule_ids: [],
        distinct_oos_wafer_count: 0,
        distinct_ooc_wafer_count: 0,
        has_r03_consec: false,
        sibling_alarm_counts: {},
      },
      equipment_context: null,
      document_hits: [],
      batch_incident_plans: [],
      upstream: [],
      errors: [],
    },
    r03_fdc_evidence: run.r03_fdc_evidence ?? null,
    llm_model: run.llm_model ?? 'mock-fixture',
    input_tokens: run.input_tokens ?? null,
    output_tokens: run.output_tokens ?? null,
    latency_ms: run.latency_ms ?? 2040,
    tool_calls: RUN_TOOL_CALLS.map(canonicalToolCall),
    action,
    approval,
  }
}

export function createAgentRun(alarmId) {
  if (USE_MOCK) {
    const run = RUNS.find((item) => item.alarm_ids.includes(alarmId))
    if (!run) return mockResponse(null)
    const detail = canonicalRunDetail(run)
    return mockResponse({
      agent_run_id: detail.agent_run_id,
      thread_id: detail.thread_id,
      incident: detail.incident,
      requested_alarm_id: alarmId,
      representative_alarm_id: detail.representative_alarm_id,
      status: 'RUNNING',
    })
  }
  return apiClient.post('/agent/runs', { alarm_id: alarmId }).then((response) => response.data)
}

export function getRuns(filter = {}) {
  if (USE_MOCK) {
    let items = RUNS.map(canonicalRunItem)
    if (filter.status) items = items.filter((item) => item.status === filter.status)
    if (filter.equipment_id) items = items.filter((item) => item.equipment_id === filter.equipment_id)
    if (filter.chamber_id) items = items.filter((item) => item.incident.chamber_id === filter.chamber_id)
    if (filter.date_from) items = items.filter((item) => item.started_at >= filter.date_from)
    if (filter.date_to) items = items.filter((item) => item.started_at <= filter.date_to)
    items.sort((a, b) => b.started_at.localeCompare(a.started_at) || b.agent_run_id.localeCompare(a.agent_run_id))
    return mockResponse(page(items, filter))
  }
  return apiClient.get('/agent/runs', { params: filter }).then((response) => response.data)
}

export function getRun(runId) {
  if (USE_MOCK) return mockResponse(canonicalRunDetail(RUNS.find((item) => item.agent_run_id === runId)))
  return apiClient.get(`/agent/runs/${runId}`).then((response) => response.data)
}

export function getApprovals(filter = {}) {
  if (USE_MOCK) {
    let items = APPROVALS.map(canonicalApproval)
    const status = filter.status ?? 'PENDING'
    if (status) items = items.filter((item) => item.status === status)
    items.sort((a, b) => b.requested_at.localeCompare(a.requested_at) || b.approval_id.localeCompare(a.approval_id))
    return mockResponse(page(items, filter))
  }
  return apiClient.get('/approvals', { params: filter }).then((response) => response.data)
>>>>>>> Stashed changes
}

// POST /approvals/{approval_id}/decision — decision(APPROVE/REJECT)·decided_by·decision_comment?
export function decideApproval(approvalId, { decision, decided_by, decision_comment }) {
  if (USE_MOCK) {
<<<<<<< Updated upstream
    const p = APPROVALS.find((x) => x.approval_id === approvalId)
    const approved = decision === 'APPROVE'
    return mockResponse({
      approval_id: approvalId,
      action_id: p?.action_id ?? null,
      approval_status: approved ? 'APPROVED' : 'REJECTED',
      send_status: approved ? 'SENDING' : 'CANCELED',
      agent_run_status: approved ? 'RUNNING' : 'COMPLETED',
      decided_by,
      decided_at: toIso('2026-06-04 07:49:00'),
      decision_comment: decision_comment ?? null,
=======
    const approval = APPROVALS.find((item) => item.approval_id === approvalId)
    const approvalStatus = decision === 'APPROVE' ? 'APPROVED' : 'REJECTED'
    return mockResponse({
      approval_id: approvalId,
      action_id: approval?.action_id ?? null,
      approval_status: approvalStatus,
      send_status: decision === 'APPROVE' ? 'WAITING' : 'CANCELED',
      agent_run_status: decision === 'APPROVE' ? 'RUNNING' : 'COMPLETED',
      decided_by,
      decided_at: '2026-06-04T07:31:23+09:00',
      decision_comment: decision_comment || null,
>>>>>>> Stashed changes
    })
  }
  return apiClient
    .post(`/approvals/${approvalId}/decision`, { decision, decided_by, decision_comment })
    .then((response) => response.data)
}

<<<<<<< Updated upstream
// GET /actions — approval_status?·send_status?·action_code?·equipment_id?·chamber_id?·date_from?·date_to?·page·size
export function getActions(params = {}) {
  if (USE_MOCK) {
    const { approval_status, send_status, action_code, equipment_id, chamber_id, date_from, date_to, ...pageParams } =
      params
    const rows = ACTIONS.filter(
      (a) =>
        (!approval_status || a.approval_status === approval_status) &&
        (!send_status || a.send_status === send_status) &&
        (!action_code || a.action_code === action_code) &&
        (!equipment_id || a.equipment_id === equipment_id) &&
        (!chamber_id || a.incident.chamber_id === chamber_id) &&
        (!date_from || a.created_at >= date_from) &&
        (!date_to || a.created_at <= date_to),
    )
      .map(isoAction)
      .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.action_id.localeCompare(a.action_id))
    return mockResponse(paginate(rows, pageParams))
  }
  return apiClient.get('/actions', { params }).then((r) => r.data)
=======
export function getActions(filter = {}) {
  if (USE_MOCK) {
    let items = ACTIONS.map(canonicalActionItem)
    for (const key of ['approval_status', 'send_status', 'action_code', 'equipment_id', 'chamber_id']) {
      if (!filter[key]) continue
      items = items.filter((item) =>
        key === 'chamber_id'
          ? item.incident.chamber_id === filter[key]
          : key === 'equipment_id'
            ? item.equipment_id === filter[key]
            : item[key] === filter[key],
      )
    }
    if (filter.date_from) items = items.filter((item) => item.created_at >= filter.date_from)
    if (filter.date_to) items = items.filter((item) => item.created_at <= filter.date_to)
    items.sort((a, b) => b.created_at.localeCompare(a.created_at) || b.action_id.localeCompare(a.action_id))
    return mockResponse(page(items, filter))
  }
  return apiClient.get('/actions', { params: filter }).then((response) => response.data)
>>>>>>> Stashed changes
}

export function getAction(actionId) {
  if (USE_MOCK) return mockResponse(canonicalActionDetail(ACTIONS.find((item) => item.action_id === actionId)))
  return apiClient.get(`/actions/${actionId}`).then((response) => response.data)
}

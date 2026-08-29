import apiClient, { mockEnabledFor, mockResponse } from './client.js'
import { assertExactObject, compactParams, requireDatePair, requireNonEmptyString } from './contract.js'
import { CORE_AGENT_ASK, CORE_AGENT_RUN, CORE_APPROVAL, approvalAfterDecision } from './contractMocks.js'
import { toIso } from './format.js'
import { RUNS } from '../../features/agent/mock/runs.js'
import { ACTIONS, APPROVALS } from '../../features/agent/mock/actions.js'

// 백엔드 agent 라우터 구현 전까지 도메인 오버라이드로 mock 유지 가능 (client.js 참조)
const USE_MOCK = mockEnabledFor('AGENT')

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
  return apiClient.get('/agent/runs/paged', { params }).then((r) => r.data)
}

// GET /agent/runs — core bare-array contract. Status and paging belong to /paged.
export function getRunsCore(params = {}) {
  assertExactObject(params, ['date_from', 'date_to'], 'getRunsCore params')
  requireDatePair(params, 'getRunsCore params')
  const query = compactParams(params)
  if (USE_MOCK) return mockResponse([CORE_AGENT_RUN])
  return apiClient.get('/agent/runs', { params: query }).then((response) => response.data)
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
  return apiClient.get('/approvals/paged', { params }).then((r) => r.data)
}

// GET /approvals — core bare-array contract. Filters and paging belong to /paged.
export function getApprovalsCore() {
  if (USE_MOCK) return mockResponse([CORE_APPROVAL])
  return apiClient.get('/approvals').then((response) => response.data)
}

// POST /approvals/{approval_id}/decision — strict public boundary.
export function decideApprovalCanonical(approvalId, input) {
  requireNonEmptyString(approvalId, 'approval_id')
  assertExactObject(input, ['decision', 'decided_by', 'decision_comment'], 'approval decision')
  const decision = input.decision
  if (!['APPROVED', 'REJECTED'].includes(decision)) {
    throw new TypeError('decision must be APPROVED or REJECTED')
  }
  const decided_by = requireNonEmptyString(input.decided_by, 'decided_by')
  const decision_comment = input.decision_comment
  if (decision_comment != null) requireNonEmptyString(decision_comment, 'decision_comment')
  const body = compactParams({ decision, decided_by, decision_comment })
  if (USE_MOCK) {
    return mockResponse(approvalAfterDecision({ decision, decided_by, decision_comment }))
  }
  return apiClient.post(`/approvals/${encodeURIComponent(approvalId)}/decision`, body).then((r) => r.data)
}

// Existing C pages still send APPROVE|REJECT. Keep their import stable while the
// transport boundary is canonical; V5-CM-4.4-3 removes this adapter after C migrates.
export function decideApprovalFromUi(approvalId, input) {
  assertExactObject(input, ['decision', 'decided_by', 'decision_comment'], 'UI approval decision')
  const mapped = { APPROVE: 'APPROVED', REJECT: 'REJECTED' }[input.decision]
  if (!mapped) throw new TypeError('UI decision must be APPROVE or REJECT')
  return decideApprovalCanonical(approvalId, { ...input, decision: mapped })
}

export const decideApproval = decideApprovalFromUi

// POST /agent/runs — source-aware run creation; no source-less overload exists.
export function createRun(input) {
  assertExactObject(input, ['alarm'], 'createRun input')
  assertExactObject(input.alarm, ['source', 'alarm_id'], 'createRun alarm')
  const source = input.alarm.source
  if (!['TRACE', 'SUMMARY', 'R03'].includes(source)) {
    throw new TypeError('alarm.source must be TRACE, SUMMARY, or R03')
  }
  const alarm_id = requireNonEmptyString(input.alarm.alarm_id, 'alarm.alarm_id')
  const body = { alarm: { source, alarm_id } }
  if (USE_MOCK) return mockResponse({ agent_run_id: 'RUN-000002', status: 'RUNNING', alarm: body.alarm })
  return apiClient.post('/agent/runs', body).then((response) => response.data)
}

// POST /agent/ask — read-only Agent chat boundary.
export function askAgent(input) {
  assertExactObject(input, ['question'], 'askAgent input')
  const body = { question: requireNonEmptyString(input.question, 'question') }
  if (USE_MOCK) return mockResponse(CORE_AGENT_ASK)
  return apiClient.post('/agent/ask', body).then((response) => response.data)
}

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
}

export function getAction(actionId) {
  if (USE_MOCK) {
    const a = ACTIONS.find((x) => x.action_id === actionId)
    return mockResponse(a ? isoAction(a) : null)
  }
  return apiClient.get(`/actions/${actionId}`).then((r) => r.data)
}

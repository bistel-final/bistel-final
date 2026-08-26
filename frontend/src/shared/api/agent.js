import apiClient, { mockEnabledFor, mockResponse } from './client.js'
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
}

// POST /approvals/{approval_id}/decision — decision(APPROVE/REJECT)·decided_by·decision_comment?
export function decideApproval(approvalId, { decision, decided_by, decision_comment }) {
  if (USE_MOCK) {
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
    })
  }
  return apiClient
    .post(`/approvals/${approvalId}/decision`, { decision, decided_by, decision_comment })
    .then((r) => r.data)
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

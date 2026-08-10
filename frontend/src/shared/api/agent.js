import apiClient, { USE_MOCK, mockResponse } from './client.js'
import { toIso, page } from './format.js'
import { RUNS, RUN_TOOL_CALLS, RUN_NODES } from '../../features/agent/mock/runs.js'
import { ACTIONS, APPROVALS, FAULT_BY_SENSOR } from '../../features/agent/mock/actions.js'

const isoRun = (r) => ({
  ...r,
  incident: { ...r.incident, first_at: toIso(r.incident.first_at), last_at: toIso(r.incident.last_at) },
  fault: FAULT_BY_SENSOR[r.incident.sensor_id],
  tool_calls: RUN_TOOL_CALLS,
  nodes: RUN_NODES,
})

const isoAction = (a) => ({
  ...a,
  created_at: toIso(a.created_at),
  approved_at: toIso(a.approved_at),
  alarm_count: a.alarm_ids.length,
})

export function getRuns() {
  if (USE_MOCK) return mockResponse(page(RUNS.map(isoRun)))
  return apiClient.get('/agent/runs').then((r) => r.data)
}

export function getRun(runId) {
  if (USE_MOCK) {
    const r = RUNS.find((x) => x.run_id === runId)
    return mockResponse(r ? isoRun(r) : null)
  }
  return apiClient.get(`/agent/runs/${runId}`).then((r) => r.data)
}

// 승인 요청 목록 — APR ID는 요청 시각 오름차순 재부여본 (fixture 참조)
export function getApprovals() {
  if (USE_MOCK) {
    const byAction = Object.fromEntries(ACTIONS.map((a) => [a.action_id, a]))
    const items = APPROVALS.map((p) => {
      const a = byAction[p.action_id]
      return {
        ...p,
        requested_at: toIso(p.requested_at),
        decided_at: toIso(p.decided_at),
        action_code: a.action_code,
        severity: a.severity,
        incident: a.incident,
      }
    })
    return mockResponse(page(items))
  }
  return apiClient.get('/approvals').then((r) => r.data)
}

export function decideApproval(approvalId, { decision, decided_by, comment }) {
  if (USE_MOCK) return mockResponse({ approval_id: approvalId, status: decision, decided_by, comment })
  return apiClient
    .post(`/approvals/${approvalId}/decision`, { decision, decided_by, comment })
    .then((r) => r.data)
}

export function getActions(filter = {}) {
  if (USE_MOCK) return mockResponse(page(ACTIONS.map(isoAction), filter))
  return apiClient.get('/actions', { params: filter }).then((r) => r.data)
}

export function getAction(actionId) {
  if (USE_MOCK) {
    const a = ACTIONS.find((x) => x.action_id === actionId)
    return mockResponse(a ? isoAction(a) : null)
  }
  return apiClient.get(`/actions/${actionId}`).then((r) => r.data)
}

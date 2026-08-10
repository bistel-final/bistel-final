import apiClient, { USE_MOCK, mockResponse } from './client.js'
import { toIso, page } from './format.js'
import { RUNS, RUN_TOOL_CALLS, RUN_NODES } from '../../features/agent/mock/runs.js'
import { ACTIONS, APPROVALS, FAULT_BY_SENSOR } from '../../features/agent/mock/actions.js'

// incident 키는 (lot_id, chamber_id) 뿐이다 (설계 4.1).
// 파라미터·구간·집계는 식별 키가 아니라 형제 필드로 올린다.
const flatIncident = ({ lot_id, chamber_id, sensor_id, recipe_step_name, first_at, last_at, alarm_count }) => ({
  incident: { lot_id, chamber_id },
  sensor_id,
  recipe_step_name,
  incident_first_at: toIso(first_at),
  incident_last_at: toIso(last_at),
  alarm_count,
})

const isoRun = (r) => {
  const inc = flatIncident(r.incident)
  return {
    ...r,
    ...inc,
    fault: FAULT_BY_SENSOR[inc.sensor_id],
    tool_calls: RUN_TOOL_CALLS,
    nodes: RUN_NODES,
  }
}

const isoAction = (a) => ({
  ...a,
  ...flatIncident(a.incident),
  created_at: toIso(a.created_at),
  approved_at: toIso(a.approved_at),
  alarm_count: a.alarm_ids.length,
})

export function getRuns(filter = {}) {
  if (USE_MOCK) return mockResponse(page(RUNS.map(isoRun), filter))
  return apiClient.get('/agent/runs', { params: filter }).then((r) => r.data)
}

export function getRun(runId) {
  if (USE_MOCK) {
    const r = RUNS.find((x) => x.run_id === runId)
    return mockResponse(r ? isoRun(r) : null)
  }
  return apiClient.get(`/agent/runs/${runId}`).then((r) => r.data)
}

// 승인 요청 목록 — APR ID는 요청 시각 오름차순 재부여본 (fixture 참조)
export function getApprovals(filter = {}) {
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
        ...flatIncident(a.incident),
      }
    })
    const hit = filter.status ? items.filter((p) => p.status === filter.status) : items
    return mockResponse(page(hit, filter))
  }
  return apiClient.get('/approvals', { params: filter }).then((r) => r.data)
}

export function decideApproval(approvalId, { decision, decided_by, decision_comment }) {
  if (USE_MOCK) {
    return mockResponse({ approval_id: approvalId, status: decision, decided_by, decision_comment })
  }
  return apiClient
    .post(`/approvals/${approvalId}/decision`, { decision, decided_by, decision_comment })
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

import apiClient, { USE_MOCK, mockResponse } from './client.js'
import { RUNS } from '../../features/agent/mock/runs.js'
import { APPROVALS, SEND_HISTORY } from '../../features/agent/mock/approvals.js'
import { ACTION_DISPLAY } from '../../features/agent/mock/actions.js'

export function getRuns() {
  if (USE_MOCK) return mockResponse(RUNS)
  return apiClient.get('/agent/runs').then((r) => r.data)
}

export function getRun(id) {
  if (USE_MOCK) return mockResponse(RUNS.find((r) => r.run_id === id) ?? null)
  return apiClient.get(`/agent/runs/${id}`).then((r) => r.data)
}

export function getApprovals() {
  if (USE_MOCK) return mockResponse({ approvals: APPROVALS, history: SEND_HISTORY })
  return apiClient.get('/approvals').then((r) => r.data)
}

export function decideApproval(id, { decision, decided_by, comment }) {
  if (USE_MOCK) return mockResponse({ id, decision, decided_by, comment, ok: true })
  return apiClient
    .post(`/approvals/${id}/decision`, { decision, decided_by, comment })
    .then((r) => r.data)
}

export function getAction(id) {
  if (USE_MOCK) return mockResponse(ACTION_DISPLAY[id] ?? null)
  return apiClient.get(`/actions/${id}`).then((r) => r.data)
}

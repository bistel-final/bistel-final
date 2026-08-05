import apiClient, { USE_MOCK, mockResponse } from './client.js'
import { NL_QUERIES } from '../../features/analytics/mock/queries.js'
import { AUDIT_LOGS } from '../../features/analytics/mock/auditLogs.js'

export function postQuery(question) {
  if (USE_MOCK) return mockResponse(NL_QUERIES[question] ?? null)
  return apiClient.post('/analytics/query', { question }).then((r) => r.data)
}

export function validateSql(sql) {
  if (USE_MOCK) return mockResponse({ valid: true, message: '재검증 통과: SELECT-only · LIMIT 강제' })
  return apiClient.post('/analytics/validate', { sql }).then((r) => r.data)
}

// mock에서는 전체 로그 반환 — 필터링·페이지네이션은 화면에서 즉시 수행 (dc.html 동작 동일)
export function getAuditLogs(filter) {
  if (USE_MOCK) return mockResponse(AUDIT_LOGS)
  return apiClient.get('/audit-logs', { params: filter }).then((r) => r.data)
}

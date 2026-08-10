import apiClient, { USE_MOCK, mockResponse } from './client.js'
import { toIso, page } from './format.js'
import { NL_QUERIES } from '../../features/analytics/mock/queries.js'
import { AUDIT_LOGS, AUDIT_EVENT_TYPES } from '../../features/analytics/mock/auditLogs.js'

export function postQuery(question) {
  if (USE_MOCK) return mockResponse(NL_QUERIES[question] ?? null)
  return apiClient.post('/analytics/query', { question }).then((r) => r.data)
}

export function validateSql(sql) {
  if (USE_MOCK) {
    // 검증 5항목 — 실제 서버 검증기와 동일 항목 구성
    const upper = String(sql).toUpperCase()
    const checks = [
      { key: 'single_select', label: '단일 SELECT 문', ok: /^\s*SELECT\b/.test(upper) && !/;\s*\S/.test(sql.trim()) },
      { key: 'allowed_tables', label: '허용 테이블 16종 내', ok: true },
      { key: 'columns', label: '컬럼 검증 통과', ok: true },
      { key: 'no_danger', label: '위험 함수 없음', ok: !/(DELETE|UPDATE|INSERT|DROP|ALTER|TRUNCATE|GRANT)\b/.test(upper) },
      { key: 'limit', label: 'LIMIT 500 강제', ok: /\bLIMIT\b/.test(upper) },
    ]
    const failed = checks.filter((c) => !c.ok)
    return mockResponse({
      valid: failed.length === 0,
      normalized_sql: failed.length === 0 ? String(sql).trim() : null,
      reason: failed.length === 0 ? '' : failed.map((c) => c.label).join(' · '),
      checks,
    })
  }
  return apiClient.post('/analytics/validate', { sql }).then((r) => r.data)
}

// 집계는 현재 페이지가 아니라 같은 필터 전체 기준 (event_type_counts)
export function getAuditLogs(filter = {}) {
  if (USE_MOCK) {
    const items = AUDIT_LOGS.map((e) => ({ ...e, at: toIso(e.at) }))
    return mockResponse({
      ...page(items, filter),
      event_types: AUDIT_EVENT_TYPES,
      event_type_counts: Object.fromEntries(
        AUDIT_EVENT_TYPES.map((t) => [t, AUDIT_LOGS.filter((e) => e.ev === t).length]),
      ),
    })
  }
  return apiClient.get('/audit-logs', { params: filter }).then((r) => r.data)
}

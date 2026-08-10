import apiClient, { USE_MOCK, mockResponse } from './client.js'
import { toIso } from './format.js'
import { NL_QUERIES, NL_REJECTS } from '../../features/analytics/mock/queries.js'
import { AUDIT_LOGS, AUDIT_EVENT_TYPES } from '../../features/analytics/mock/auditLogs.js'

// POST /analytics/query — 응답: question·sql·columns·rows(dict[])·row_count·metric·metric_result·group_by·visualization·latency_ms
export function postQuery(question) {
  if (USE_MOCK) return mockResponse(NL_QUERIES[question] ?? NL_REJECTS[question] ?? null)
  return apiClient.post('/analytics/query', { question }).then((r) => r.data)
}

// POST /analytics/validate — 응답: valid·normalized_sql?·reason·checks[]
export function validateSql(sql) {
  if (USE_MOCK) {
    const upper = String(sql).toUpperCase()
    const checks = [
      { key: 'single_select', label: '단일 SELECT', ok: /^\s*SELECT\b/.test(upper) && !/;\s*\S/.test(String(sql).trim()) },
      { key: 'allowed_tables', label: '허용 테이블', ok: !/DOCUMENT_CHUNK|AUDIT_LOG|APPROVAL_REQUEST/.test(upper) },
      { key: 'columns', label: '컬럼 검증', ok: true },
      { key: 'no_danger', label: '위험 함수 없음', ok: !/(DELETE|UPDATE|INSERT|DROP|ALTER|TRUNCATE|GRANT)\b/.test(upper) },
      { key: 'limit', label: 'LIMIT 500 강제', ok: /\bLIMIT\b/.test(upper) },
    ]
    const valid = checks.every((c) => c.ok)
    return mockResponse({
      valid,
      normalized_sql: valid ? sql : null,
      reason: valid ? '' : 'POLICY_REJECTED: 검증 실패',
      checks,
    })
  }
  return apiClient.post('/analytics/validate', { sql }).then((r) => r.data)
}

// GET /audit-logs — event_type?·actor_type?·date_from?·date_to?·page·size
// entity_id 는 명세에 없어 클라이언트에서 거른다. TODO(api): entity_id 파라미터 제안 반영 대기
export function getAuditLogs(params = {}) {
  if (USE_MOCK) {
    const { event_type, actor_type, date_from, date_to, entity_id, page: p = 1, size = 20 } = params
    const filtered = AUDIT_LOGS.filter(
      (e) =>
        (!event_type || e.event_type === event_type) &&
        (!actor_type || e.actor_type === actor_type) &&
        (!date_from || e.occurred_at.slice(0, 10) >= date_from) &&
        (!date_to || e.occurred_at.slice(0, 10) <= date_to) &&
        (!entity_id || String(e.entity_id).toLowerCase().includes(String(entity_id).toLowerCase())),
    )
    const items = filtered
      .slice()
      .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at) || b.audit_id - a.audit_id)
      .slice((p - 1) * size, p * size)
      .map((e) => ({ ...e, occurred_at: toIso(e.occurred_at) }))
    return mockResponse({
      items,
      total: filtered.length,
      page: p,
      size,
      event_types: AUDIT_EVENT_TYPES,
      // 현재 페이지가 아니라 같은 필터 전체의 집계
      event_type_counts: Object.fromEntries(
        AUDIT_EVENT_TYPES.map((t) => [t, filtered.filter((e) => e.event_type === t).length]),
      ),
    })
  }
  return apiClient.get('/audit-logs', { params }).then((r) => r.data)
}

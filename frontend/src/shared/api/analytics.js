import apiClient, { ANALYTICS_QUERY_TIMEOUT_MS, mockEnabledFor, mockResponse } from './client.js'
import { assertExactObject, compactParams, requireDatePair } from './contract.js'
import { CORE_AUDIT_LOG } from './contractMocks.js'
import { page, toIso } from './format.js'
import { AUDIT_EVENT_TYPES, AUDIT_LOGS } from '../../features/analytics/mock/auditLogs.js'
import { NL_INITIAL_HISTORY, NL_QUERIES, NL_REJECTS } from '../../features/analytics/mock/queries.js'

const USE_MOCK = mockEnabledFor('ANALYTICS')

// POST /analytics/query — 응답은 API v3 canonical(AnalysisQueryResponse):
//   generated_sql·columns·rows·row_count·metric·metric_result·group_by·visualization
//   ·is_valid·is_rejected·reject_reason·error_msg·latency_ms·nl_query_log_id
// mock 데이터는 v2 시절 필드명(sql·rejected·reason)이므로 canonical 로 정규화해
// 화면은 항상 한 가지 계약만 본다.
// 기록을 남겨 멱등하지 않으므로 GET 이 아니다. LLM 최대 2회 시도(self-correction)를
// 감당하도록 요청별 timeout 을 늘린다.
const toCanonicalQuery = (d) =>
  d == null
    ? null
    : {
        ...d,
        generated_sql: d.generated_sql ?? d.sql ?? null,
        is_rejected: d.is_rejected ?? d.rejected ?? false,
        reject_reason: d.reject_reason ?? d.reason ?? null,
        error_msg: d.error_msg ?? null,
      }

export function postQuery(question) {
  if (USE_MOCK)
    return mockResponse(NL_QUERIES[question] ?? NL_REJECTS[question] ?? null).then(toCanonicalQuery)
  return apiClient
    .post('/analytics/query', { question }, { timeout: ANALYTICS_QUERY_TIMEOUT_MS })
    .then((response) => toCanonicalQuery(response.data))
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
  return apiClient.post('/analytics/validate', { sql }).then((response) => response.data)
}

// GET /analytics/history — is_valid?·is_rejected?·date_from?·date_to?·page·size
export function getQueryHistory(filter = {}) {
  if (USE_MOCK) {
    let items = NL_INITIAL_HISTORY.map((history, index) => ({
      nl_query_log_id: index + 1,
      asked_at: `2026-06-04T07:${String(40 - index).padStart(2, '0')}:00+09:00`,
      question: history.q,
      generated_sql: history.ok ? (NL_QUERIES[history.q]?.sql ?? null) : null,
      is_valid: history.ok,
      is_rejected: !history.ok,
      reject_reason: history.ok ? null : history.code,
      row_cnt: history.rows,
      latency_ms: history.lat,
      error_msg: null,
    }))
    if (filter.is_valid != null) items = items.filter((item) => item.is_valid === filter.is_valid)
    if (filter.is_rejected != null) items = items.filter((item) => item.is_rejected === filter.is_rejected)
    const dateFrom = String(filter.date_from ?? '').slice(0, 10)
    const dateTo = String(filter.date_to ?? '').slice(0, 10)
    if (dateFrom) items = items.filter((item) => item.asked_at.slice(0, 10) >= dateFrom)
    if (dateTo) items = items.filter((item) => item.asked_at.slice(0, 10) <= dateTo)
    items.sort((a, b) => b.asked_at.localeCompare(a.asked_at) || b.nl_query_log_id - a.nl_query_log_id)
    return mockResponse(page(items, filter))
  }
  return apiClient.get('/analytics/history', { params: filter }).then((response) => response.data)
}

// GET /analytics/evaluations — latest=true·page·size (골드 평가 결과 파일이 없으면 200 + items=[])
export function getEvaluations(filter = { latest: true }) {
  if (USE_MOCK) return mockResponse({ items: [], total: 0, page: filter.page ?? 1, size: filter.size ?? 20 })
  return apiClient.get('/analytics/evaluations', { params: filter }).then((response) => response.data)
}

// GET /audit-logs/paged — event_type?·actor_type?·entity_type?·entity_id?·date_from?·date_to?·page·size
// 화면 3 subview 는 페이지·집계가 필요해 선택 확장 /paged 를 소비한다 (API v3 5.2).
// 호환 필수 GET /audit-logs 는 bare array 로 별도 제공된다 (API v3 3.8).
// entity_id 는 부분 일치로 거른다.
export function getAuditLogsPaged(params = {}) {
  if (USE_MOCK) {
    const { event_type, actor_type, entity_type, date_from, date_to, entity_id, page: p = 1, size = 20 } = params
    const filtered = AUDIT_LOGS.filter(
      (e) =>
        (!event_type || e.event_type === event_type) &&
        (!actor_type || e.actor_type === actor_type) &&
        (!entity_type || e.entity_type === entity_type) &&
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
  return apiClient.get('/audit-logs/paged', { params }).then((response) => response.data)
}

// Existing D page import remains stable until its domain-owned canonical transition.
export const getAuditLogs = getAuditLogsPaged

// GET /audit-logs — core bare-array compatibility contract.
export function getAuditLogsCore(params = {}) {
  assertExactObject(
    params,
    ['event_type', 'actor_type', 'entity_type', 'entity_id', 'date_from', 'date_to'],
    'getAuditLogsCore params',
  )
  requireDatePair(params, 'getAuditLogsCore params')
  const query = compactParams(params)
  if (USE_MOCK) return mockResponse([CORE_AUDIT_LOG])
  return apiClient.get('/audit-logs', { params: query }).then((response) => response.data)
}

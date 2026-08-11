<<<<<<< Updated upstream
import apiClient, { USE_MOCK, mockResponse } from './client.js'
import { toIso } from './format.js'
import { NL_QUERIES, NL_REJECTS } from '../../features/analytics/mock/queries.js'
import { AUDIT_LOGS, AUDIT_EVENT_TYPES } from '../../features/analytics/mock/auditLogs.js'
=======
import apiClient, { ANALYTICS_QUERY_TIMEOUT_MS, USE_MOCK, mockResponse } from './client.js'
import { page, toIso } from './format.js'
import { AUDIT_EVENT_TYPES, AUDIT_LOGS } from '../../features/analytics/mock/auditLogs.js'
import { NL_INITIAL_HISTORY, NL_QUERIES } from '../../features/analytics/mock/queries.js'

const rowObjects = (columns, rows) =>
  (rows ?? []).map((row) => Object.fromEntries(columns.map((column, index) => [column, row[index]])))

function canonicalQuery(question, definition, index = 0) {
  if (!definition) return null
  const rejected = Boolean(definition.reject)
  const columns = definition.columns ?? definition.cols ?? []
  const rows = rowObjects(columns, definition.rows)
  const metricColumn = columns.at(-1) ?? null
  const groupBy = columns.length > 1 ? [columns[0]] : []
  const metricResult = groupBy.length
    ? rows.map((row) => ({
        group: Object.fromEntries(groupBy.map((column) => [column, row[column]])),
        value: Number(row[metricColumn]),
      }))
    : (rows[0]?.[metricColumn] ?? null)
  return {
    question,
    sql: rejected ? null : definition.sql,
    columns: rejected ? [] : columns,
    rows: rejected ? [] : rows,
    row_count: rejected ? 0 : rows.length,
    metric: rejected ? null : { type: 'count', column: metricColumn, p: null },
    metric_result: rejected ? null : metricResult,
    group_by: rejected ? [] : groupBy,
    visualization: rejected
      ? null
      : (definition.visualization ?? { chart_type: 'table', x: null, y: metricColumn }),
    is_valid: !rejected,
    is_rejected: rejected,
    reject_reason: rejected ? (definition.reject_reason ?? definition.reject_code) : null,
    error_msg: null,
    latency_ms: definition.latency_ms ?? definition.lat ?? 0,
    nl_query_log_id: index + 1,
  }
}
>>>>>>> Stashed changes

// POST /analytics/query — 응답: question·sql·columns·rows(dict[])·row_count·metric·metric_result·group_by·visualization·latency_ms
export function postQuery(question) {
<<<<<<< Updated upstream
  if (USE_MOCK) return mockResponse(NL_QUERIES[question] ?? NL_REJECTS[question] ?? null)
  return apiClient.post('/analytics/query', { question }).then((r) => r.data)
=======
  if (USE_MOCK) {
    const entries = Object.entries(NL_QUERIES)
    const index = entries.findIndex(([savedQuestion]) => savedQuestion === question)
    return mockResponse(canonicalQuery(question, index >= 0 ? entries[index][1] : null, index))
  }
  return apiClient
    .post('/analytics/query', { question }, { timeout: ANALYTICS_QUERY_TIMEOUT_MS })
    .then((response) => response.data)
>>>>>>> Stashed changes
}

// POST /analytics/validate — 응답: valid·normalized_sql?·reason·checks[]
export function validateSql(sql) {
  if (USE_MOCK) {
    const upper = String(sql).toUpperCase()
    const checks = [
<<<<<<< Updated upstream
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
=======
      { key: 'single_select', label: '단일 SELECT 문', ok: /^\s*SELECT\b/.test(upper) && !/;\s*\S/.test(sql.trim()) },
      { key: 'allowed_tables', label: '허용 테이블 16종 내', ok: true },
      { key: 'columns', label: '컬럼 검증 통과', ok: true },
      {
        key: 'no_danger',
        label: '위험 함수 없음',
        ok: !/(DELETE|UPDATE|INSERT|DROP|ALTER|TRUNCATE|GRANT)\b/.test(upper),
      },
      { key: 'limit', label: 'LIMIT 500 강제', ok: /\bLIMIT\b/.test(upper) },
    ]
    const failed = checks.filter((check) => !check.ok)
    return mockResponse({
      valid: failed.length === 0,
      normalized_sql: failed.length === 0 ? String(sql).trim() : null,
      reason: failed.length === 0 ? '' : failed.map((check) => check.label).join(' · '),
>>>>>>> Stashed changes
      checks,
    })
  }
  return apiClient.post('/analytics/validate', { sql }).then((response) => response.data)
}

<<<<<<< Updated upstream
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
=======
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

export function getEvaluations(filter = { latest: true }) {
  if (USE_MOCK) return mockResponse({ items: [], total: 0, page: filter.page ?? 1, size: filter.size ?? 20 })
  return apiClient.get('/analytics/evaluations', { params: filter }).then((response) => response.data)
}

const parseState = (value) => {
  if (value == null || typeof value === 'object') return value
  const match = String(value).match(/^\s*([^=]+?)\s*=\s*(.+?)\s*$/)
  return match ? { [match[1].trim()]: match[2].trim() } : { value: String(value) }
}

const canonicalAuditItem = (event, index) => ({
  audit_id: index + 1,
  occurred_at: toIso(event.occurred_at ?? event.at),
  actor_type: event.actor_type ?? event.ac,
  actor_id: event.actor_id ?? event.subject ?? null,
  event_type: event.event_type ?? event.ev,
  entity_type: event.entity_type ?? event.entity ?? null,
  entity_id: event.entity_id ?? null,
  before: parseState(event.before),
  after: parseState(event.after),
  detail: event.detail ?? event.note ?? null,
})

export function getAuditLogs(filter = {}) {
  if (USE_MOCK) {
    let items = AUDIT_LOGS.map(canonicalAuditItem)
    for (const key of ['event_type', 'actor_type', 'entity_type', 'entity_id']) {
      if (filter[key]) items = items.filter((item) => item[key] === filter[key])
    }
    const dateFrom = String(filter.date_from ?? '').slice(0, 10)
    const dateTo = String(filter.date_to ?? '').slice(0, 10)
    if (dateFrom) items = items.filter((item) => item.occurred_at.slice(0, 10) >= dateFrom)
    if (dateTo) items = items.filter((item) => item.occurred_at.slice(0, 10) <= dateTo)
    items.sort((a, b) => b.occurred_at.localeCompare(a.occurred_at) || b.audit_id - a.audit_id)
    const responsePage = page(items, filter)
    return mockResponse({
      ...responsePage,
      event_types: [...AUDIT_EVENT_TYPES],
      event_type_counts: Object.fromEntries(
        AUDIT_EVENT_TYPES.map((eventType) => [eventType, items.filter((item) => item.event_type === eventType).length]),
      ),
    })
  }
  return apiClient.get('/audit-logs', { params: filter }).then((response) => response.data)
>>>>>>> Stashed changes
}

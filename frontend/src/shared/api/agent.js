import apiClient, { mockEnabledFor, mockResponse } from './client.js'
import { assertExactObject, compactParams, requireDatePair, requireNonEmptyString } from './contract.js'
import {
  CORE_AGENT_ASK,
  CORE_AGENT_RUN,
  CORE_APPROVAL,
  CORE_CHAMBER_GRAPH,
  approvalAfterDecision,
} from './contractMocks.js'
import { toIso } from './format.js'
import { APPROVALS } from '../../features/agent/mock/actions.js'
import { adaptActionForLegacyPage, adaptRunForLegacyPage, hasDeliveryStatus } from '../../features/agent/agent-run-view-state.js'
import { PUBLIC_ACTIONS } from '../../features/agent/mock/publicActions.js'

// 백엔드 agent 라우터 구현 전까지 도메인 오버라이드로 mock 유지 가능 (client.js 참조)
const USE_MOCK = mockEnabledFor('AGENT')
const GRAPH_EVIDENCE_RELATION_ID = CORE_CHAMBER_GRAPH.relationships.find(
  (relationship) => relationship.from_node_id === 'Parameter:PH_FOCUS',
).relation_id

const GRAPH_EVIDENCE = Object.freeze({
  type: 'GRAPH',
  source_id: GRAPH_EVIDENCE_RELATION_ID,
  title: '챔버 측정 관계',
  excerpt: `${CORE_AGENT_RUN.chamber_id}의 파라미터 측정 관계를 판단 근거로 사용했습니다.`,
  relation_id: GRAPH_EVIDENCE_RELATION_ID,
  graph_revision: CORE_CHAMBER_GRAPH.graph_revision,
})

const CORE_AGENT_RUN_DETAIL = Object.freeze({
  ...CORE_AGENT_RUN,
  evidence_items: [
    {
      type: 'ALARM',
      source_id: 'R03:R03-f41e6518529e8ed5e6a9',
      title: '연속 OOS 알람',
      excerpt: '같은 챔버·파라미터·Recipe Step에서 연속 3 WAFER OOS가 발생했습니다.',
      alarm: { source: 'R03', alarm_id: 'R03-f41e6518529e8ed5e6a9' },
    },
    {
      type: 'TRACE',
      source_id: 'LH-000004',
      title: '대표 Trace',
      excerpt: '대표 알람의 측정 Trace를 판정에 사용했습니다.',
    },
    {
      type: 'DOCUMENT',
      source_id: 'DOC-TROUBLE-FDC:cs2:0006',
      title: 'RFM 진단 가이드',
      excerpt: '반사파 상승과 RF 정합 상태를 우선 점검합니다.',
      document_id: 'DOC-TROUBLE-FDC',
      chunk_id: 'DOC-TROUBLE-FDC:cs2:0006',
      section: '3. 대표 이상 유형별 진단 > 3.2 RFM',
    },
    GRAPH_EVIDENCE,
  ],
  approval: {
    approval_id: CORE_APPROVAL.approval_id,
    action_id: CORE_APPROVAL.action_id,
    agent_run_id: CORE_APPROVAL.agent_run_id,
    status: CORE_APPROVAL.status,
    decided_by: CORE_APPROVAL.decided_by,
    decided_at: CORE_APPROVAL.decided_at,
    decision_comment: CORE_APPROVAL.decision_comment,
  },
  action: {
    action_id: CORE_AGENT_RUN.action_id,
    agent_run_id: CORE_AGENT_RUN.agent_run_id,
    action_code: CORE_AGENT_RUN.recommended_action,
    reason: CORE_APPROVAL.reason,
    approval_status: CORE_APPROVAL.status,
    deliveries: CORE_AGENT_RUN.deliveries,
  },
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

// GET /agent/runs — core bare-array contract. There is no /agent/runs/paged endpoint.
export function getRunsCore(params = {}, options = {}) {
  assertExactObject(params, ['date_from', 'date_to', 'status', 'predicted_fault_code'], 'getRunsCore params')
  requireDatePair(params, 'getRunsCore params')
  const query = compactParams(params)
  if (USE_MOCK) {
    const rows = [CORE_AGENT_RUN].filter(
      (run) =>
        (!query.status || run.status === query.status) &&
        (!query.predicted_fault_code || run.predicted_fault_code === query.predicted_fault_code) &&
        (!query.date_from || run.created_at.slice(0, 10) >= query.date_from) &&
        (!query.date_to || run.created_at.slice(0, 10) <= query.date_to),
    )
    return mockResponse(rows)
  }
  return apiClient.get('/agent/runs', { params: query, signal: options.signal }).then((response) => response.data)
}

// Deprecated page adapter. Equipment/chamber filtering and paging stay client-side
// because API v3 intentionally exposes a bare-array endpoint only.
export function getRuns(params = {}) {
  const { page = 1, size = 20, equipment_id, chamber_id, ...coreParams } = params
  return getRunsCore(coreParams).then((rows) => {
    const adapted = rows
      .map(adaptRunForLegacyPage)
      .filter(
        (run) =>
          (!equipment_id || run.equipment_id === equipment_id) &&
          (!chamber_id || run.incident.chamber_id === chamber_id),
      )
      .sort((a, b) => b.started_at.localeCompare(a.started_at) || b.agent_run_id.localeCompare(a.agent_run_id))
    return paginate(adapted, { page, size })
  })
}

export function getRun(agentRunId, options = {}) {
  const normalizedId = requireNonEmptyString(agentRunId, 'agent_run_id')
  if (USE_MOCK) {
    if (normalizedId === CORE_AGENT_RUN.agent_run_id) return mockResponse(CORE_AGENT_RUN_DETAIL)
    return mockResponse(null)
  }
  return apiClient
    .get(`/agent/runs/${encodeURIComponent(normalizedId)}`, { signal: options.signal })
    .then((response) => response.data)
}

// Legacy page adapter. Real transport uses core GET /approvals and wraps the bare array locally.
export function getApprovals(params = {}) {
  if (USE_MOCK) {
    const { status, ...pageParams } = params
    const rows = APPROVALS.filter((p) => !status || p.status === status)
      .map(isoApproval)
      .sort((a, b) => b.requested_at.localeCompare(a.requested_at) || b.approval_id.localeCompare(a.approval_id))
    return mockResponse(paginate(rows, pageParams))
  }
  const { status, ...pageParams } = params
  return getApprovalsCore().then((rows) => paginate(rows.filter((approval) => !status || approval.status === status), pageParams))
}

// GET /approvals — core bare-array contract. There is no /approvals/paged endpoint.
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

export function getActionsCore(params = {}) {
  assertExactObject(params, ['action_code'], 'getActionsCore params')
  const query = compactParams(params)
  if (USE_MOCK) {
    return mockResponse(PUBLIC_ACTIONS.filter((action) => !query.action_code || action.action_code === query.action_code))
  }
  return apiClient.get('/actions', { params: query }).then((response) => response.data)
}

// Deprecated page adapter for the existing Action screen.
export function getActions(params = {}) {
  const {
    page = 1,
    size = 20,
    approval_status,
    send_status,
    action_code,
    equipment_id,
    chamber_id,
    date_from,
    date_to,
  } = params
  return getActionsCore({ action_code }).then((rows) => {
    const adapted = rows
      .map(adaptActionForLegacyPage)
      .filter(
        (action) =>
          (!approval_status || action.approval_status === approval_status) &&
          (!send_status || hasDeliveryStatus(action, send_status)) &&
          (!equipment_id || action.equipment_id === equipment_id) &&
          (!chamber_id || action.chamber_id === chamber_id) &&
          (!date_from || action.created_at.slice(0, 10) >= date_from) &&
          (!date_to || action.created_at.slice(0, 10) <= date_to),
      )
      .sort((a, b) => b.created_at.localeCompare(a.created_at) || b.action_id.localeCompare(a.action_id))
    return paginate(adapted, { page, size })
  })
}

export function getAction(actionId) {
  const normalizedId = requireNonEmptyString(actionId, 'action_id')
  if (USE_MOCK) {
    const action = PUBLIC_ACTIONS.find((item) => item.action_id === normalizedId)
    return mockResponse(
      action
        ? {
            ...action,
            deliveries: action.deliveries.map((delivery) => ({
              ...delivery,
              started_at: action.created_at,
              completed_at: delivery.status === 'SENT' ? action.created_at : null,
            })),
          }
        : null,
    )
  }
  return apiClient.get(`/actions/${encodeURIComponent(normalizedId)}`).then((response) => response.data)
}

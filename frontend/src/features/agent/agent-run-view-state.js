const ACTION_SEVERITY = Object.freeze({
  MONITORING: 'LOW',
  WARNING: 'MEDIUM',
  EQP_HOLD: 'HIGH',
})

export const actionSeverity = (actionCode) => ACTION_SEVERITY[actionCode] ?? null

export const hasDeliveryStatus = (action, status) =>
  (action?.deliveries ?? []).some((delivery) => delivery.status === status)

const JUDGEMENT_BY_SOURCE = Object.freeze({
  SUMMARY: 'OOC',
  TRACE: 'OOS',
  R03: 'OOS',
})

export const alarmJudgement = (run, alarm) =>
  alarm?.judgement ?? JUDGEMENT_BY_SOURCE[run?.representative_alarm_source ?? run?.alarm_source] ?? null

export const measuredText = (value, fallback = '실측 미제공') => {
  if (value == null) return fallback
  if (typeof value === 'string' && (!value.trim() || value.trim().toLowerCase() === 'null')) return fallback
  return value
}

export const selectInitialRun = (runs) =>
  runs.find((run) => run.status === 'WAITING_APPROVAL') ?? runs[0] ?? null

export const shouldPollAgentRun = (run) =>
  run?.status === 'RUNNING' ||
  (run?.action?.deliveries ?? []).some((delivery) =>
    delivery.status === 'WAITING' || delivery.status === 'SENDING')

export function trendUnavailableMessage({
  hasAlarmEvidence,
  alarmFound,
  alarmLookupFailed,
  queryReady,
  traceFailed,
  traceCount,
}) {
  if (!hasAlarmEvidence) return '이 실행에 대표 알람 근거가 없습니다.'
  if (alarmLookupFailed) return 'Detection 알람 상세 조회에 실패했습니다.'
  if (!alarmFound) return '현재 Detection 데이터에 이 실행의 알람이 없어 트렌드를 표시할 수 없습니다.'
  if (!queryReady) return '트렌드 조회에 필요한 LOT·WAFER·PARAMETER 실측값이 없습니다.'
  if (traceFailed) return 'Detection trace 조회에 실패했습니다.'
  if (traceCount === 0) return '이 알람의 trace 실측 데이터가 없습니다.'
  return null
}

export function evidenceHref(item, context = {}) {
  if (item.type === 'DOCUMENT') {
    const query = new URLSearchParams({
      document_id: item.document_id,
      chunk_id: item.chunk_id,
      view: 'agent-evidence',
    })
    return `/documents?${query}`
  }
  if (item.type === 'ALARM') {
    const query = new URLSearchParams({ source: item.alarm.source })
    return `/alarms/${encodeURIComponent(item.alarm.alarm_id)}?${query}`
  }
  if (item.type === 'GRAPH' && context.chamberId) {
    const query = new URLSearchParams({
      chamber_id: context.chamberId,
      relation_id: item.relation_id,
      graph_revision: item.graph_revision,
    })
    return `/ontology?${query}`
  }
  return null
}

const IMPACT_NODE_LABEL = Object.freeze({
  CHAMBER: 'Chamber',
  SIBLING_CHAMBER: 'Chamber',
  PARAMETER: 'Parameter',
  PROCESS_STEP: 'ProcessStep',
  EQUIPMENT: 'Equipment',
  AREA: 'Area',
  MODEL: 'EquipmentModel',
  EQUIPMENT_MODEL: 'EquipmentModel',
})

const impactNodeIds = (items) => [...new Set((items ?? []).flatMap((item) => {
  const label = IMPACT_NODE_LABEL[item.kind]
  return label && item.source_id ? [`${label}:${item.source_id}`] : []
}))]

export function impactOntologySelection(detail, chamberId) {
  const graphEvidence = detail?.evidence_items?.find(
    (item) => item.type === 'GRAPH' && item.graph_revision,
  )
  if (!chamberId || !graphEvidence || detail?.impact_scope?.status !== 'AVAILABLE') return null
  const directNodeIds = impactNodeIds(detail.impact_scope.direct)
  const checkNodeIds = impactNodeIds(detail.impact_scope.check_required)
  if (directNodeIds.length + checkNodeIds.length === 0) return null
  return {
    chamberId,
    graphRevision: graphEvidence.graph_revision,
    directNodeIds,
    checkNodeIds,
  }
}

export function impactOntologyHref(detail, chamberId) {
  const selection = impactOntologySelection(detail, chamberId)
  if (!selection) return null
  const query = new URLSearchParams({
    chamber_id: selection.chamberId,
    graph_revision: selection.graphRevision,
  })
  if (selection.directNodeIds.length > 0) query.set('direct_node_ids', selection.directNodeIds.join(','))
  if (selection.checkNodeIds.length > 0) query.set('check_node_ids', selection.checkNodeIds.join(','))
  return `/ontology?${query}`
}

export function approvalViewState(state, event) {
  if (event.type === 'SUBMIT') {
    if (state.phase === 'pending' || state.phase === 'success') return state
    return { ...state, phase: 'pending', error: null }
  }
  if (event.type === 'SUCCESS') return { phase: 'success', status: event.status, error: null }
  if (event.type === 'CONFLICT') return { ...state, phase: 'conflict', error: null }
  if (event.type === 'FAILURE') return { ...state, phase: 'error', error: event.message }
  if (event.type === 'RESET') return { phase: 'idle', status: event.status ?? null, error: null }
  return state
}

export function adaptRunForLegacyPage(run) {
  const chamberId = run.chamber_id
  return {
    ...run,
    started_at: run.created_at,
    incident_first_at: run.created_at,
    incident_last_at: run.created_at,
    incident: { lot_id: run.lot_id ?? null, chamber_id: chamberId },
    equipment_id: run.equipment_id ?? String(chamberId ?? '').replace(/-PM\d+$/, ''),
    tool_calls: run.tools ?? [],
  }
}

export function adaptActionForLegacyPage(action) {
  return {
    ...action,
    incident: { lot_id: action.lot_id, chamber_id: action.chamber_id },
    severity: actionSeverity(action.action_code),
  }
}

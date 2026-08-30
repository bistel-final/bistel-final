const CANONICAL_FIELDS = Object.freeze({
  AlarmItem: [
    'action_code',
    'alarm_id',
    'alarm_type',
    'area',
    'chamber_id',
    'cl',
    'equipment_id',
    'lcl',
    'lot_id',
    'mes_status',
    'notify_status',
    'occurred_at',
    'parameter_id',
    'predicted_fault_code',
    'recipe_id',
    'recipe_step_no',
    'rule_code',
    'seq_no',
    'source',
    'statistic_type',
    'ucl',
    'value',
    'wafer_id',
  ],
  ParameterItem: [
    'area',
    'ctrl_lower',
    'ctrl_upper',
    'parameter_id',
    'parameter_name',
    'spec_lower',
    'spec_upper',
    'target_value',
    'unit',
    'upper_only',
  ],
  DocumentHit: ['chunk_id', 'content', 'document_id', 'model_code', 'score', 'section', 'title'],
  AgentRunItem: [
    'action_id',
    'agent_run_id',
    'alarm_id',
    'alarm_source',
    'approval_id',
    'chamber_id',
    'confidence',
    'created_at',
    'deliveries',
    'latency_ms',
    'llm_model',
    'predicted_fault_code',
    'recommended_action',
    'status',
    'tools',
  ],
  ApprovalItem: [
    'action_code',
    'action_id',
    'agent_run_id',
    'approval_id',
    'chamber_id',
    'created_at',
    'decided_at',
    'decided_by',
    'decision_comment',
    'equipment_id',
    'lot_id',
    'predicted_fault_code',
    'reason',
    'status',
  ],
  AuditLogItem: [
    'actor_id',
    'actor_type',
    'after',
    'audit_id',
    'before',
    'detail',
    'entity_id',
    'entity_type',
    'event_type',
    'occurred_at',
  ],
  AgentAskResponse: [
    'answer',
    'confidence',
    'evidence_items',
    'limitations',
    'predicted_fault_code',
    'recommended_action',
    'title',
    'tools',
  ],
  PublicDeliveryItem: ['channel', 'status'],
  AlarmEvidence: ['excerpt', 'source_id', 'title', 'type'],
  TraceEvidence: ['excerpt', 'source_id', 'title', 'type'],
  GraphEvidence: ['excerpt', 'graph_revision', 'relation_id', 'source_id', 'title', 'type'],
  DocumentEvidence: ['chunk_id', 'document_id', 'excerpt', 'section', 'source_id', 'title', 'type'],
  MetrologyEvidence: ['excerpt', 'source_id', 'title', 'type'],
})

const EVIDENCE_COMPONENT_BY_TYPE = Object.freeze({
  ALARM: 'AlarmEvidence',
  TRACE: 'TraceEvidence',
  GRAPH: 'GraphEvidence',
  DOCUMENT: 'DocumentEvidence',
  METROLOGY: 'MetrologyEvidence',
})

const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key)

function pickCanonical(value, component) {
  if (value == null || typeof value !== 'object' || Array.isArray(value)) {
    throw new TypeError(`${component} must be an object`)
  }
  const missing = CANONICAL_FIELDS[component].filter((field) => !own(value, field))
  if (missing.length) throw new TypeError(`${component} missing canonical fields: ${missing.join(', ')}`)
  return Object.fromEntries(CANONICAL_FIELDS[component].map((field) => [field, structuredClone(value[field])]))
}

const projectAutoTool = (tool) => {
  for (const field of ['tool_name', 'status', 'result_summary']) {
    if (!own(tool, field)) throw new TypeError(`AutoToolCallItem missing canonical field: ${field}`)
  }
  return { tool_name: tool.tool_name, status: tool.status, result_summary: tool.result_summary }
}

const projectChatTool = (tool) => {
  for (const field of ['tool_name', 'status', 'result_summary']) {
    if (!own(tool, field)) throw new TypeError(`ChatToolCallItem missing canonical field: ${field}`)
  }
  return { tool_name: tool.tool_name, status: tool.status, result_summary: tool.result_summary }
}

const projectEvidence = (evidence) => {
  const component = EVIDENCE_COMPONENT_BY_TYPE[evidence?.type]
  if (!component) throw new TypeError(`EvidenceItem has an unknown type: ${String(evidence?.type)}`)
  return pickCanonical(evidence, component)
}

export const projectAlarm = (value) => pickCanonical(value, 'AlarmItem')
export const projectParameter = (value) => pickCanonical(value, 'ParameterItem')
export const projectDocumentHit = (value) => pickCanonical(value, 'DocumentHit')
export const projectApproval = (value) => pickCanonical(value, 'ApprovalItem')
export const projectAuditLog = (value) => pickCanonical(value, 'AuditLogItem')

export function projectAgentRun(value) {
  const projected = pickCanonical(value, 'AgentRunItem')
  projected.tools = value.tools.map(projectAutoTool)
  projected.deliveries = value.deliveries.map((delivery) => pickCanonical(delivery, 'PublicDeliveryItem'))
  return projected
}

export function projectAgentAsk(value) {
  const projected = pickCanonical(value, 'AgentAskResponse')
  projected.tools = value.tools.map(projectChatTool)
  projected.evidence_items = value.evidence_items.map(projectEvidence)
  return projected
}

export { CANONICAL_FIELDS }

// API v3 examples expressed as server-shaped mock values. These values include the
// one-revision compatibility aliases because the Backend Router owns that wire shape.
// Frontend projections never derive or read those aliases.

export const CORE_ALARM = Object.freeze({
  alarm_id: 'TAL-0001',
  source: 'TRACE',
  alarm_type: 'OOS',
  rule_code: 'TRACE_OOS',
  area: 'Etch',
  equipment_id: 'EQP04',
  equipment: 'EQP04',
  chamber_id: 'EQP04-PM2',
  chamber: 'EQP04-PM2',
  recipe_id: 'RECIPE04',
  recipe: 'RECIPE04',
  lot_id: 'LOT004',
  lot: 'LOT004',
  wafer_id: 'LOT004W002',
  wafer: 'LOT004W002',
  parameter_id: 'ET_REFL',
  parameter: 'ET_REFL',
  recipe_step_no: 1,
  step_no: 1,
  value: 37.467,
  seq_no: 0,
  statistic_type: null,
  cl: null,
  ucl: null,
  lcl: null,
  occurred_at: '2026-08-04T06:52:29+09:00',
  predicted_fault_code: null,
  fault: null,
  action_code: null,
  notify_status: null,
  notify: false,
  mes_status: null,
  mes: '',
})

export const CORE_TRACE_POINT = Object.freeze({
  measured_at: '2026-08-04T06:52:29+09:00',
  value: 37.467,
  recipe_step_no: 1,
  seq_no: 0,
})

export const CORE_PARAMETER = Object.freeze({
  parameter_id: 'PH_FOCUS',
  parameter_name: 'Focus Offset',
  name: 'Focus Offset',
  area: 'Photo',
  unit: 'nm',
  spec_lower: -60,
  LSL: -60,
  ctrl_lower: -36,
  LCL: -36,
  target_value: 0,
  TARGET: 0,
  ctrl_upper: 36,
  UCL: 36,
  spec_upper: 60,
  USL: 60,
  upper_only: false,
})

export const CORE_DOCUMENT_HIT = Object.freeze({
  document_id: 'DOC-SPEC-PH9000',
  doc_id: 'DOC-SPEC-PH9000',
  chunk_id: 'DOC-SPEC-PH9000:cs2:0005',
  title: 'PH-9000 Photo Scanner 장비 스펙 및 운전 기준',
  section: '4. 파라미터별 상세 > 4.2 Focus Offset (`PH_FOCUS`)',
  score: 0.650960803031926,
  content:
    'Focus Offset은 초점이 맞는 면에서 벗어난 정도다. 초점이 벗어나면 패턴 경계가 흐려지고 현상 후 CD가 작게 형성될 수 있다. `PH_FOCUS` 이상은 `FOC` 후보로 우선 검토하고, 웨이퍼 척 표면 이물, 웨이퍼 평탄도, 포커스 센서 교정 상태를 확인한다.',
  model_code: 'PH-9000',
})

export const CORE_AGENT_RUN = Object.freeze({
  agent_run_id: 'RUN-000001',
  created_at: '2026-08-04T07:00:30+09:00',
  alarm_source: 'R03',
  alarm_id: 'R03-f41e6518529e8ed5e6a9',
  chamber_id: 'EQP04-PM2',
  chamber: 'EQP04-PM2',
  predicted_fault_code: 'RFM',
  fault_code: 'RFM',
  fault_name: null,
  fault_color: null,
  confidence: 0.84,
  recommended_action: 'EQP_HOLD',
  status: 'WAITING_APPROVAL',
  action_id: 'ACT-000003',
  approval_id: 'APR-000001',
  tools: [
    {
      tool_name: 'get_fdc_summary',
      status: 'SUCCESS',
      result_summary: 'Summary context loaded',
      n: 'get_fdc_summary',
      s: 'SUCCESS',
    },
  ],
  deliveries: [
    { channel: 'EMAIL', status: 'SENT' },
    { channel: 'MES', status: 'BLOCKED' },
  ],
  latency_ms: 920,
  llm_model: 'configured-model',
})

export const CORE_APPROVAL = Object.freeze({
  approval_id: 'APR-000001',
  agent_run_id: 'RUN-000001',
  action_id: 'ACT-000003',
  created_at: '2026-08-04T07:00:40+09:00',
  lot_id: 'LOT004',
  lot: 'LOT004',
  equipment_id: 'EQP04',
  equipment: 'EQP04',
  chamber_id: 'EQP04-PM2',
  chamber: 'EQP04-PM2',
  predicted_fault_code: 'RFM',
  fault_code: 'RFM',
  action_code: 'EQP_HOLD',
  reason: 'R03_CONSEC: 같은 chamber·parameter·recipe step에서 연속 3 WAFER OOS',
  status: 'PENDING',
  decided_by: null,
  decided_at: null,
  decision_comment: null,
  approved_by: null,
  approved_at: null,
})

export function approvalAfterDecision({ decision, decided_by, decision_comment = null }) {
  return {
    ...CORE_APPROVAL,
    status: decision,
    decided_by,
    decided_at: '2026-08-04T10:21:10+09:00',
    decision_comment,
    approved_by: decided_by,
    approved_at: '2026-08-04T10:21:10+09:00',
  }
}

export const CORE_AUDIT_LOG = Object.freeze({
  audit_id: 101,
  occurred_at: '2026-08-04T10:21:10+09:00',
  at: '2026-08-04T10:21:10+09:00',
  actor_type: 'HUMAN',
  actor: 'HUMAN',
  actor_id: 'operator',
  event_type: 'APPROVAL_DECIDED',
  entity_type: 'APPROVAL',
  entity_id: 'APR-000001',
  event: 'APPROVE',
  entity: 'APPROVAL:APR-000001',
  before: { status: 'PENDING' },
  after: { status: 'APPROVED' },
  detail: null,
})

export const CORE_AGENT_ASK = Object.freeze({
  title: 'EQP04-PM2 anomaly analysis',
  answer: 'R03 알람과 장비·문서 근거를 함께 확인했습니다.',
  tools: [
    {
      tool_name: 'get_equipment_context',
      status: 'SUCCESS',
      result_summary: 'topology evidence loaded',
      name: 'get_equipment_context',
      result: 'topology evidence loaded',
    },
  ],
  predicted_fault_code: 'RFM',
  confidence: 0.84,
  recommended_action: 'EQP_HOLD',
  evidence_items: [
    {
      type: 'DOCUMENT',
      source_id: 'DOC-TROUBLE-FDC:cs2:0006',
      title: 'FDC 이상 유형 진단 및 조치 가이드',
      excerpt:
        '`ET_REFL` 이상은 `RFM` 후보로 우선 검토한다. 반사파가 증가하면 플라즈마에 전달되는 실효 전력이 줄어 식각 부족으로 이어질 수 있다.',
      document_id: 'DOC-TROUBLE-FDC',
      chunk_id: 'DOC-TROUBLE-FDC:cs2:0006',
      section: '3. 대표 이상 유형별 진단 > 3.2 RFM — RF Mismatch (RF 정합 이상)',
    },
  ],
  limitations: ['Pilot scope; production ground truth unavailable'],
  evidence: {
    doc_id: 'DOC-TROUBLE-FDC',
    document_id: 'DOC-TROUBLE-FDC',
    chunk_id: 'DOC-TROUBLE-FDC:cs2:0006',
    section: '3. 대표 이상 유형별 진단 > 3.2 RFM — RF Mismatch (RF 정합 이상)',
  },
  limit: 'Pilot scope; production ground truth unavailable',
})

const RELATION_ID = 'REL-9687560b5876022b2512'

export const CORE_CHAMBER_GRAPH = Object.freeze({
  context: {
    area: 'Photo',
    equipment_id: 'EQP01',
    chamber_id: 'EQP01-PM1',
    model_code: 'PH-9000',
    process_step_id: 'CT-PHOTO',
    adjacent_process_step_ids: ['CT-ETCH'],
    parameter_ids: ['PH_DEV', 'PH_DOSE', 'PH_FOCUS', 'PH_PEB'],
    relation_ids: [
      'REL-47fcae63de255c114f5d',
      'REL-d2de931b285063c7a8ef',
      RELATION_ID,
      'REL-75e65f542c456ff70886',
    ],
  },
  graph_revision: '3474debee491ea5c699080109d748a4922ad0566a3b84568e9067053de2fa2eb',
  nodes: [
    {
      node_id: 'Chamber:EQP01-PM1',
      label: 'Chamber',
      business_id: 'EQP01-PM1',
      name: 'EQP01-PM1',
      properties: {},
    },
    {
      node_id: 'Parameter:PH_DEV',
      label: 'Parameter',
      business_id: 'PH_DEV',
      name: 'PH_DEV',
      properties: {},
    },
    {
      node_id: 'Parameter:PH_DOSE',
      label: 'Parameter',
      business_id: 'PH_DOSE',
      name: 'PH_DOSE',
      properties: {},
    },
    {
      node_id: 'Parameter:PH_FOCUS',
      label: 'Parameter',
      business_id: 'PH_FOCUS',
      name: 'PH_FOCUS',
      properties: {},
    },
    {
      node_id: 'Parameter:PH_PEB',
      label: 'Parameter',
      business_id: 'PH_PEB',
      name: 'PH_PEB',
      properties: {},
    },
  ],
  relationships: [
    {
      relation_id: 'REL-47fcae63de255c114f5d',
      type: 'MEASURED_ON',
      from_node_id: 'Parameter:PH_DEV',
      to_node_id: 'Chamber:EQP01-PM1',
    },
    {
      relation_id: 'REL-d2de931b285063c7a8ef',
      type: 'MEASURED_ON',
      from_node_id: 'Parameter:PH_DOSE',
      to_node_id: 'Chamber:EQP01-PM1',
    },
    {
      relation_id: RELATION_ID,
      type: 'MEASURED_ON',
      from_node_id: 'Parameter:PH_FOCUS',
      to_node_id: 'Chamber:EQP01-PM1',
    },
    {
      relation_id: 'REL-75e65f542c456ff70886',
      type: 'MEASURED_ON',
      from_node_id: 'Parameter:PH_PEB',
      to_node_id: 'Chamber:EQP01-PM1',
    },
  ],
  node_count: 5,
  relationship_count: 4,
})

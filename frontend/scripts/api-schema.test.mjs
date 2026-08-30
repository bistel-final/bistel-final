// mock 응답의 키 집합이 API 명세(시스템설계서 10장 DTO)와 같은지 검사한다.
// mock↔실서버 동치성 회귀 장치 — 스키마가 갈라지면 여기서 먼저 깨진다.
import assert from 'node:assert/strict'

const detection = await import('../src/shared/api/detection.js')
const agent = await import('../src/shared/api/agent.js')
const analytics = await import('../src/shared/api/analytics.js')

const PAGE = ['items', 'total', 'page', 'size']

// [라벨, 호출, 최상위 키, 첫 item 키(선택)]
const CASES = [
  [
    'GET /dashboard/summary',
    () => detection.getDashboard(),
    ['reference_date', 'area', 'hierarchy', 'sensor_catalog', 'alarm_count', 'oos_count', 'ooc_count',
      'daily_trend', 'top_sensors', 'equipment_counts', 'pending_approvals', 'recent_alarms'],
  ],
  [
    'GET /alarms',
    () => detection.getAlarms(),
    PAGE,
    ['alarm_id', 'lot_hist_id', 'lot_id', 'wafer_no', 'chamber_id', 'equipment_id', 'sensor_id',
      'recipe_step_no', 'recipe_step_name', 'rule_id', 'judgement', 'hit_cnt', 'detail', 'occurred_at',
      'incident', 'action_id', 'action_code', 'approval_status', 'latest_agent_run_id', 'agent_run_status'],
  ],
  ['GET /traces/catalog', () => detection.getTraceCatalog(), ['areas', 'equipments', 'sensors', 'recipes', 'lots', 'anomaly']],
  [
    'POST /traces/search',
    () => detection.searchTraces({ chamber_id: 'PHO-01-C1', sensor_ids: ['PH_FOCUS'], lot_id: 'LOT-260007' }),
    ['wafers', 'limits', 'total', 'measured_step_stats'],
  ],
  [
    'GET /agent/runs',
    () => agent.getRuns(),
    PAGE,
    ['agent_run_id', 'thread_id', 'requested_alarm_id', 'representative_alarm_id', 'alarm_ids', 'alarm_count',
      'incident', 'equipment_id', 'sensor_id', 'recipe_step_name', 'incident_first_at', 'incident_last_at',
      'status', 'fault_code', 'fault_name', 'fault_name_en', 'cause_summary', 'recommended_action',
      'action_reason', 'severity', 'approval_required', 'confidence', 'action_id', 'llm_model', 'latency_ms',
      'started_at', 'ended_at', 'tool_calls', 'nodes'],
  ],
  [
    'GET /actions',
    () => agent.getActions(),
    PAGE,
    ['action_id', 'agent_run_id', 'incident', 'equipment_id', 'sensor_id', 'alarm_ids', 'alarm_count',
      'trigger_alarm_lot_hist_id', 'action_code', 'reason', 'severity', 'approval_required', 'approval_status',
      'approved_by', 'approved_at', 'send_channel', 'send_status', 'send_attempt_count', 'sent_at', 'created_at'],
  ],
  [
    'GET /approvals',
    () => agent.getApprovals(),
    PAGE,
    ['approval_id', 'agent_run_id', 'action_id', 'incident', 'equipment_id', 'sensor_id', 'action_code',
      'severity', 'requested_at', 'status', 'decided_by', 'decided_at', 'decision_comment'],
  ],
  [
    'POST /approvals/{id}/decision',
    () => agent.decideApproval('APR-0002', { decision: 'APPROVE', decided_by: 'bang', decision_comment: null }),
    ['approval_id', 'action_id', 'approval_status', 'send_status', 'agent_run_status', 'decided_by', 'decided_at', 'decision_comment'],
  ],
  [
    'GET /audit-logs/paged',
    () => analytics.getAuditLogs(),
    [...PAGE, 'event_types', 'event_type_counts'],
    ['audit_id', 'occurred_at', 'actor_type', 'actor_id', 'event_type', 'entity_type', 'entity_id', 'before', 'after', 'detail'],
  ],
  [
    'POST /analytics/query',
    () => analytics.postQuery('챔버별 알람 건수 내림차순'),
    ['question', 'sql', 'columns', 'rows', 'row_count', 'metric', 'metric_result', 'group_by', 'visualization', 'latency_ms'],
  ],
  ['POST /analytics/validate', () => analytics.validateSql('SELECT 1 LIMIT 500'), ['valid', 'normalized_sql', 'reason', 'checks']],
]

let failed = 0
for (const [label, call, topKeys, itemKeys] of CASES) {
  const res = await call()
  try {
    assert.deepEqual([...Object.keys(res)].sort(), [...topKeys].sort(), `${label} 최상위 키 불일치`)
    if (itemKeys) {
      const first = res.items?.[0]
      assert.ok(first, `${label} items 비어 있음`)
      assert.deepEqual([...Object.keys(first)].sort(), [...itemKeys].sort(), `${label} item 키 불일치`)
    }
    console.log('OK  ', label)
  } catch (e) {
    failed += 1
    console.error('FAIL', label, '\n   ', e.message)
  }
}

// incident 는 (lot_id, chamber_id) 두 개뿐이어야 한다 — 센서를 넣으면 조치 중복 제거가 깨진다
for (const [label, call] of [
  ['alarms', () => detection.getAlarms()],
  ['runs', () => agent.getRuns()],
  ['actions', () => agent.getActions()],
  ['approvals', () => agent.getApprovals()],
]) {
  const res = await call()
  const keys = Object.keys(res.items[0].incident).sort()
  try {
    assert.deepEqual(keys, ['chamber_id', 'lot_id'], `${label}.incident 키가 (lot_id, chamber_id)가 아님`)
    console.log('OK   incident key —', label)
  } catch (e) {
    failed += 1
    console.error('FAIL incident key —', label, '\n   ', e.message)
  }
}

// tool_calls.status 는 'SUCCESS' (레거시 'OK' 금지)
const run = (await agent.getRuns()).items[0]
try {
  assert.ok(run.tool_calls.every((t) => t.status === 'SUCCESS'), "tool_calls.status 가 'SUCCESS'가 아님")
  assert.ok(!run.tool_calls.some((t) => t.tool_name === 'decide_action'), 'decide_action 이 tool_calls 에 있음')
  assert.equal(run.thread_id, run.agent_run_id, 'thread_id 가 agent_run_id 와 다름')
  console.log('OK   tool_calls / nodes 계약')
} catch (e) {
  failed += 1
  console.error('FAIL tool_calls 계약\n   ', e.message)
}

// 센서별 한계선 — 전역 상수 금지 확인 (ET_CF4 는 하한 미달이 나와야 한다)
const cf4 = (await detection.searchTraces({ sensor_ids: ['ET_CF4'], lot_id: 'LOT-260010' }))
const lim = cf4.limits.ET_CF4
try {
  assert.equal(lim.spec_lower, 74)
  assert.equal(lim.spec_upper, 86)
  assert.equal(lim.unit, 'sccm')
  const values = cf4.wafers.flatMap((w) => w.points.map((p) => p.value))
  assert.ok(values.some((v) => v < lim.spec_lower), 'ET_CF4 하한 미달 포인트가 없음')
  console.log('OK   ET_CF4 한계선 74/86 sccm · 하한 미달 존재')
} catch (e) {
  failed += 1
  console.error('FAIL ET_CF4 한계선\n   ', e.message)
}

if (failed) {
  console.error(`\n${failed}건 실패`)
  process.exit(1)
}
console.log('\napi-schema: 전체 통과')

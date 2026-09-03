import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { getActions, getAgentEvaluations, getRun, getRunsCore } from '../src/shared/api/agent.js'
import { getAlarm, getAllAlarms, searchTraces } from '../src/shared/api/detection.js'
import { getAuditLogsCore } from '../src/shared/api/analytics.js'
import { getChamberRelationsCore, getDocument } from '../src/shared/api/knowledge.js'
import { matchTab, sortActions } from '../src/features/agent/actionsSort.js'
import {
  actionSeverity,
  adaptActionForLegacyPage,
  alarmJudgement,
  approvalViewState,
  documentHitsOf,
  evidenceHref,
  hasDeliveryStatus,
  impactOntologyHref,
  impactOntologySelection,
  measuredText,
  selectInitialRun,
  shouldPollAgentRun,
  trendUnavailableMessage,
} from '../src/features/agent/agent-run-view-state.js'
import { deliveryStatusMeta } from '../src/features/agent/delivery-flow-state.js'
import { incidentTraceScope } from '../src/shared/trace/incidentTrace.js'
import { selectedWaferChartModel, traceChartModel, traceYAxisDomain } from '../src/shared/trace/traceModel.js'
import { PUBLIC_ACTIONS } from '../src/features/agent/mock/publicActions.js'
import {
  parseOntologyFocus,
  resolveOntologyFocus,
} from '../src/features/knowledge/ontology-focus-state.js'
import {
  auditTargetsOf,
  mergeAuditItems,
} from '../src/shared/components/audit/run-audit-view-state.js'
import { runStatusVariant } from '../src/shared/components/ui/statusStyles.js'
import { alarmDisplayLabel, alarmSourceText } from '../src/features/agent/components/agentModel.js'

const action = {
  action_id: 'ACT-2',
  agent_run_id: 'RUN-2',
  created_by_agent_run_id: 'RUN-2',
  action_code: 'EQP_HOLD',
  lot_id: 'LOT002',
  equipment_id: 'EQP02',
  chamber_id: 'EQP02-PM1',
  approval_status: 'PENDING',
  deliveries: [
    { channel: 'EMAIL', status: 'SENT' },
    { channel: 'MES', status: 'WAITING' },
  ],
  created_at: '2026-08-29T13:00:00+09:00',
}

assert.equal(actionSeverity('MONITORING'), 'LOW')
assert.equal(actionSeverity('WARNING'), 'MEDIUM')
assert.equal(actionSeverity('EQP_HOLD'), 'HIGH')
assert.equal(hasDeliveryStatus(action, 'SENT'), true)
assert.equal(hasDeliveryStatus(action, 'WAITING'), true)
assert.equal(hasDeliveryStatus(action, 'FAILED'), false)
assert.equal(runStatusVariant('RUNNING'), 't-blue')
assert.equal(runStatusVariant('WAITING_APPROVAL'), 't-amber')
assert.equal(runStatusVariant('COMPLETED'), 't-green')
assert.equal(runStatusVariant('FAILED'), 't-red')
assert.equal(alarmSourceText('TRACE'), 'TRACE 알람')
assert.equal(alarmSourceText('SUMMARY'), 'SUMMARY 알람')
assert.equal(alarmSourceText('R03'), 'R03 연속 알람')
assert.equal(alarmSourceText('UNKNOWN'), '알람')
assert.equal(
  alarmDisplayLabel({
    source: 'R03',
    alarmId: 'R03-043312a49ccff7127b93',
    chamberId: 'EQP04-PM2',
    lotId: 'LOT004',
  }),
  '반복 OOS 알람 · EQP04-PM2 · LOT004',
)
assert.doesNotMatch(
  alarmDisplayLabel({ source: 'R03', alarmId: 'R03-043312a49ccff7127b93' }),
  /043312a49ccff7127b93/,
)
assert.equal(
  alarmDisplayLabel({ source: 'TRACE', alarmId: 'TAL-0138' }),
  'TRACE 알람 · TAL-0138',
)
assert.equal(alarmJudgement({ alarm_source: 'SUMMARY' }, null), 'OOC')
assert.equal(alarmJudgement({ alarm_source: 'TRACE' }, null), 'OOS')
assert.equal(alarmJudgement({ alarm_source: 'R03' }, null), 'OOS')
assert.equal(alarmJudgement({ alarm_source: 'UNKNOWN' }, null), null)
assert.equal(alarmJudgement({ alarm_source: 'SUMMARY' }, { judgement: 'IN_CONTROL' }), 'IN_CONTROL')
assert.equal(measuredText(null), '실측 미제공')
assert.equal(measuredText('null'), '실측 미제공')
assert.equal(measuredText(''), '실측 미제공')
assert.equal(measuredText(0), 0)
assert.ok(!('send_status' in adaptActionForLegacyPage(action)))
assert.ok(!('send_channel' in adaptActionForLegacyPage(action)))
assert.deepEqual(
  sortActions([...PUBLIC_ACTIONS].reverse()).map((item) => item.action_id),
  ['ACT-0005', 'ACT-0003', 'ACT-0001'],
)
assert.equal(PUBLIC_ACTIONS.filter((item) => matchTab(item, 'PENDING')).length, 1)
assert.equal(PUBLIC_ACTIONS.filter((item) => matchTab(item, 'SENT')).length, 2)
assert.equal(PUBLIC_ACTIONS.filter((item) => matchTab(item, 'WAITING')).length, 1)
assert.ok(PUBLIC_ACTIONS.every((item) => !('send_status' in item) && !('send_channel' in item)))

const focusDomain = traceYAxisDomain(
  [{ points: [{ value: 29 }, { value: 79 }] }],
  { spec_lower: -60, ctrl_lower: -36, target: 0, ctrl_upper: 36, spec_upper: 60 },
)
assert.ok(focusDomain[0] > 0 && focusDomain[0] < 29, '0을 강제하지 않고 실측 하단 여유만 둬야 합니다')
assert.ok(focusDomain[1] > 79, '실측 최댓값 위에 시각적 여유를 둬야 합니다')

const upperOnlyDomain = traceYAxisDomain(
  [{ points: [{ value: 37.467 }, { value: 39.2 }] }],
  { spec_upper: 30 },
)
assert.ok(upperOnlyDomain[0] < 30 && upperOnlyDomain[0] > 0, '가장 가까운 위반 기준선은 축에 남겨야 합니다')
assert.ok(upperOnlyDomain[1] > 39.2)
assert.deepEqual(traceYAxisDomain([{ points: [] }], null), ['auto', 'auto'])

// includeAllLimits: 다섯 한계선을 모두 그리는 화면은 실측 밖 한계까지 축에 담아야 한다.
const allLimits = { spec_lower: 0, ctrl_lower: 0, target: 8, ctrl_upper: 21, spec_upper: 30 }
const fullDomain = traceYAxisDomain([{ points: [{ value: 25 }, { value: 35 }] }], allLimits, {
  includeAllLimits: true,
})
assert.ok(fullDomain[0] <= 0, 'LSL·LCL(0)이 축 안에 있어야 그래프에 표시됩니다')
assert.ok(fullDomain[1] >= 35, '실측 최댓값은 그대로 축 안에 있어야 합니다')
const focusedDomain = traceYAxisDomain([{ points: [{ value: 25 }, { value: 35 }] }], allLimits)
assert.ok(focusedDomain[0] > 0, '기본 모드는 실측 범위에 집중하는 기존 거동을 유지해야 합니다')

const fiveWaferChart = traceChartModel(
  [1, 3, 5, 7, 9].map((waferNo) => ({
    lot_hist_id: `LH-${waferNo}`,
    sensor_id: 'ET_CF4',
    wafer_no: waferNo,
    points: [1, 2, 3].map((seqNo) => ({ seq_no: seqNo, value: 70 + waferNo + seqNo / 10 })),
  })),
)
assert.equal(fiveWaferChart.rows.length, 5, 'X축에는 5개 웨이퍼가 각각 한 번씩 나타나야 합니다')
assert.equal(fiveWaferChart.series.length, 3, '동일 seq끼리 연결한 비교선은 3개여야 합니다')
assert.deepEqual(
  fiveWaferChart.rows.map((row) => row.wafer_label),
  ['W1', 'W3', 'W5', 'W7', 'W9'],
)
assert.deepEqual(fiveWaferChart.series.map((item) => item.label), [
  'Step — · seq 1',
  'Step — · seq 2',
  'Step — · seq 3',
])
assert.equal(fiveWaferChart.rows.every((row) => fiveWaferChart.series.every((item) => Number.isFinite(row[item.key]))), true)

const selectedWaferChart = selectedWaferChartModel({
  lot_hist_id: 'LH-00181',
  wafer_no: 6,
  points: [
    { recipe_step_no: 1, recipe_step_name: 'MAIN_ETCH', seq_no: 0, measured_at: '2026-08-04T07:00:04+09:00', value: 31.758 },
    { recipe_step_no: 1, recipe_step_name: 'MAIN_ETCH', seq_no: 1, measured_at: '2026-08-04T07:00:09+09:00', value: 33.656 },
    { recipe_step_no: 2, recipe_step_name: 'OVER_ETCH', seq_no: 3, measured_at: '2026-08-04T07:00:19+09:00', value: 0.974 },
  ],
})
assert.deepEqual(selectedWaferChart.series, [{ key: 'value', label: 'W6 실측' }])
assert.deepEqual(selectedWaferChart.rows.map((row) => row.point_label), [
  '07:00:04',
  '07:00:09',
  '07:00:19',
])
assert.deepEqual(selectedWaferChart.rows.map((row) => row.value), [31.758, 33.656, 0.974])

const runs = [
  { agent_run_id: 'RUN-2', status: 'COMPLETED' },
  { agent_run_id: 'RUN-1', status: 'WAITING_APPROVAL' },
]
assert.equal(selectInitialRun(runs).agent_run_id, 'RUN-1')
assert.equal(selectInitialRun([]), null)
assert.equal(shouldPollAgentRun({ status: 'RUNNING', action: null }), true)
assert.equal(shouldPollAgentRun({ status: 'WAITING_APPROVAL', action: { deliveries: [{ status: 'WAITING' }] } }), true)
assert.equal(shouldPollAgentRun({ status: 'WAITING_APPROVAL', action: { deliveries: [{ status: 'BLOCKED' }] } }), false)
assert.equal(shouldPollAgentRun({ status: 'COMPLETED', action: { deliveries: [{ status: 'SENT' }] } }), false)
assert.deepEqual(Object.keys(['BLOCKED', 'WAITING', 'SENDING', 'SENT', 'FAILED', 'CANCELED', 'UNKNOWN'].reduce((acc, status) => ({ ...acc, [status]: deliveryStatusMeta(status).label }), {})).sort(), ['BLOCKED', 'CANCELED', 'FAILED', 'SENDING', 'SENT', 'UNKNOWN', 'WAITING'])
assert.deepEqual(
  incidentTraceScope(
    { lot_id: 'LOT1', chamber_id: 'EQP01-PM1', sensor_id: 'P2', wafer_no: 2 },
    [
      { lot_id: 'LOT1', chamber_id: 'EQP01-PM1', sensor_id: 'P1', wafer_no: 1 },
      { lot_id: 'OTHER', chamber_id: 'EQP01-PM1', sensor_id: 'P3', wafer_no: 3 },
    ],
  ),
  { chamber_id: 'EQP01-PM1', sensor_ids: ['P2', 'P1'], lot_id: 'LOT1', wafer_nos: [2, 1] },
)
assert.deepEqual(
  incidentTraceScope(
    { lot_id: 'LOT1', chamber_id: 'EQP01-PM1', parameter_id: 'P4', wafer_no: null },
    [
      { lot_id: 'LOT1', chamber_id: 'EQP01-PM1', parameter_id: 'P1', wafer_no: null },
      { lot_id: 'LOT1', chamber_id: 'EQP01-PM1', parameter_id: 'P2', wafer_no: 3 },
    ],
  ),
  { chamber_id: 'EQP01-PM1', sensor_ids: ['P4', 'P1', 'P2'], lot_id: 'LOT1', wafer_nos: [3] },
)
assert.equal(
  trendUnavailableMessage({
    hasAlarmEvidence: true,
    alarmFound: false,
    alarmLookupFailed: false,
    queryReady: false,
    traceFailed: false,
    traceCount: 0,
  }),
  '현재 Detection 데이터에 이 실행의 알람이 없어 트렌드를 표시할 수 없습니다.',
)
assert.equal(
  trendUnavailableMessage({
    hasAlarmEvidence: true,
    alarmFound: true,
    alarmLookupFailed: false,
    queryReady: true,
    traceFailed: false,
    traceCount: 1,
  }),
  null,
)
assert.deepEqual(await getRunsCore({ status: 'COMPLETED' }), [])

assert.equal(
  evidenceHref({ type: 'ALARM', alarm: { source: 'R03', alarm_id: 'SAME-ID' } }),
  '/alarms/SAME-ID?source=R03',
)
assert.equal(
  evidenceHref({ type: 'DOCUMENT', document_id: 'DOC 1', chunk_id: 'DOC 1:cs1:0001' }),
  '/documents?document_id=DOC+1&chunk_id=DOC+1%3Acs1%3A0001&view=agent-evidence',
)
assert.deepEqual(
  documentHitsOf([
    {
      type: 'DOCUMENT',
      source_id: 'DOC 1:cs1:0001',
      title: 'FDC 조치 가이드',
      document_id: 'DOC 1',
      chunk_id: 'DOC 1:cs1:0001',
      section: '3.1',
      excerpt: '점검 절차',
    },
    { type: 'GRAPH', source_id: 'REL-1' },
  ]),
  {
    hits: [{
      source_id: 'DOC 1:cs1:0001',
      title: 'FDC 조치 가이드',
      document_id: 'DOC 1',
      chunk_id: 'DOC 1:cs1:0001',
      section: '3.1',
      content: '점검 절차',
      score: null,
      href: '/documents?document_id=DOC+1&chunk_id=DOC+1%3Acs1%3A0001&view=agent-evidence',
    }],
  },
)

const documentsPageSource = readFileSync(
  new URL('../src/features/knowledge/pages/DocumentsPage.jsx', import.meta.url),
  'utf8',
)
const documentDetailDrawerSource = readFileSync(
  new URL('../src/features/knowledge/components/DocumentDetailDrawer.jsx', import.meta.url),
  'utf8',
)
assert.match(
  documentsPageSource,
  /urlDocumentHandledRef\.current === urlDocumentKey\) urlDocumentHandledRef\.current = ''/,
  'React Strict Mode에서 취소된 문서 딥링크는 다음 effect가 다시 처리할 수 있어야 합니다',
)
assert.match(
  documentDetailDrawerSource,
  /wide \? 'left-\[296px\]' : 'w-\[1008px\] max-w-\[calc\(100%-296px\)\]'/,
  '넓은 문서 상세는 탐색 패널을 제외한 나머지 영역을 채워야 합니다',
)
assert.match(documentsPageSource, /detail_view: 'library'/)
assert.match(documentsPageSource, /wide=\{urlDetailView === 'agent-evidence' \|\| urlDetailView === 'library'\}/)
assert.match(documentsPageSource, /function FilterChips/)
assert.match(documentsPageSource, /aria-pressed=\{selected\}/)
assert.doesNotMatch(documentsPageSource, /<select/)
assert.match(documentsPageSource, /format=\{\(item\) => `\$\{item\}개`\}/)
assert.match(documentsPageSource, /setInput\(nextQuery\)/)
assert.match(documentsPageSource, /clearInput: false/)
const graphEvidence = {
  type: 'GRAPH',
  relation_id: 'REL-9687560b5876022b2512',
  graph_revision: '3474debee491ea5c699080109d748a4922ad0566a3b84568e9067053de2fa2eb',
}
assert.equal(
  evidenceHref(graphEvidence, { chamberId: 'EQP04-PM2' }),
  '/ontology?chamber_id=EQP04-PM2&relation_id=REL-9687560b5876022b2512&graph_revision=3474debee491ea5c699080109d748a4922ad0566a3b84568e9067053de2fa2eb',
)
assert.equal(evidenceHref({ type: 'TRACE', source_id: 'LH-1' }), null)

const graphParams = new URLSearchParams(evidenceHref(graphEvidence, { chamberId: 'EQP04-PM2' }).split('?')[1])
const graphFocus = parseOntologyFocus(graphParams)
assert.equal(graphFocus.phase, 'ready')
const contextualGraph = await getChamberRelationsCore('EQP04-PM2')
assert.equal(contextualGraph.context.chamber_id, 'EQP04-PM2')
const resolvedFocus = resolveOntologyFocus(contextualGraph, graphFocus)
assert.equal(resolvedFocus.phase, 'found')
assert.equal(resolvedFocus.relation.id, graphEvidence.relation_id)
assert.equal(resolvedFocus.source.business_id, 'ET_REFL')
assert.equal(resolvedFocus.target.business_id, 'EQP04-PM2')
assert.equal(
  resolveOntologyFocus(contextualGraph, { ...graphFocus, graphRevision: 'stale' }).phase,
  'revision-mismatch',
)
assert.equal(parseOntologyFocus(new URLSearchParams('chamber_id=EQP04-PM2')).phase, 'invalid')

const runDetail = await getRun('RUN-000001')
assert.ok(runDetail.evidence_items.some((item) => item.type === 'GRAPH'))
assert.equal(runDetail.action.deliveries.length, 2)
assert.deepEqual(runDetail.tools.map((item) => item.tool_name), [
  'get_fdc_summary',
  'get_equipment_context',
  'search_documents',
])
assert.equal(runDetail.evidence_items.find((item) => item.type === 'TRACE').source_id, 'LH-00177')
assert.equal(runDetail.prediction.cause_summary, '반사파 상승과 RF 정합 이상 가능성이 가장 높습니다.')
assert.ok(runDetail.action.deliveries.every((item) => 'started_at' in item && 'completed_at' in item))
assert.deepEqual(
  ['diagnosis', 'evidence_assessment', 'impact_scope', 'similar_incidents', 'post_action_observation']
    .filter((field) => runDetail[field] != null),
  ['diagnosis', 'evidence_assessment', 'impact_scope', 'similar_incidents', 'post_action_observation'],
)
assert.equal(runDetail.diagnosis.status, 'AVAILABLE')
assert.equal(runDetail.evidence_assessment.status, 'SUFFICIENT')
assert.equal(runDetail.impact_scope.status, 'AVAILABLE')
assert.equal(runDetail.similar_incidents.status, 'EMPTY')
assert.equal(runDetail.post_action_observation.status, 'NOT_AVAILABLE_STATIC_DATASET')
const impactHref = impactOntologyHref(runDetail, 'EQP04-PM2')
assert.ok(impactHref?.startsWith('/ontology?'))
assert.deepEqual(impactOntologySelection(runDetail, 'EQP04-PM2'), {
  chamberId: 'EQP04-PM2',
  graphRevision: contextualGraph.graph_revision,
  directNodeIds: ['Chamber:EQP04-PM2', 'Parameter:ET_REFL'],
  checkNodeIds: ['ProcessStep:CT-ETCH', 'Chamber:EQP04-PM1'],
})
const impactFocus = parseOntologyFocus(new URLSearchParams(impactHref.split('?')[1]))
assert.equal(impactFocus.kind, 'impact')
assert.deepEqual(impactFocus.directNodeIds, ['Chamber:EQP04-PM2', 'Parameter:ET_REFL'])
assert.deepEqual(impactFocus.checkNodeIds, ['ProcessStep:CT-ETCH', 'Chamber:EQP04-PM1'])
const resolvedImpact = resolveOntologyFocus(contextualGraph, impactFocus)
assert.equal(resolvedImpact.phase, 'found')
assert.deepEqual(resolvedImpact.directNodes.map((node) => node.business_id), ['EQP04-PM2', 'ET_REFL'])
assert.deepEqual(resolvedImpact.checkNodes.map((node) => node.business_id), ['CT-ETCH', 'EQP04-PM1'])

const evaluation = await getAgentEvaluations()
assert.equal(evaluation.fault_5class_empty_reason, null)
assert.equal(evaluation.golden_flow_empty_reason, null)
assert.equal(evaluation.fault_5class.label_source, 'SYNTHETIC_GENERATOR')
assert.equal(evaluation.fault_5class.classification.population_count, 7)
assert.equal(evaluation.fault_5class.exclusions.find((item) => item.reason === 'NO_INJECTED_FAULT').count, 5)
assert.equal(evaluation.golden_flow.phases.length, 7)
assert.equal(evaluation.golden_flow.phases.at(-1).phase, 'SECOND_BATCH')
assert.doesNotMatch(JSON.stringify(evaluation), /ground_truth_fault_code|artifact.*path|sha256/i)

const linkedDocument = await getDocument('DOC-TROUBLE-FDC')
assert.ok(linkedDocument.chunks.some((chunk) => chunk.chunk_id === 'DOC-TROUBLE-FDC:cs2:0006'))

const linkedAlarm = await getAlarm('R03-f41e6518529e8ed5e6a9', 'R03')
assert.equal(linkedAlarm.alarm_id, 'R03-f41e6518529e8ed5e6a9')
assert.equal(linkedAlarm.source, 'R03')
assert.equal(linkedAlarm.latest_agent_run_id, 'RUN-000001')
assert.equal(linkedAlarm.lot_hist_id, 'LH-00177')
assert.equal(await getAlarm('R03-f41e6518529e8ed5e6a9', 'TRACE'), null)

const finalIncidentAlarms = await getAllAlarms({ chamber_id: 'EQP04-PM2' })
assert.deepEqual(finalIncidentAlarms.items.map((item) => item.wafer_no), [2, 4, 6])
const finalTraceScope = incidentTraceScope(linkedAlarm, finalIncidentAlarms.items)
assert.deepEqual(finalTraceScope, {
  chamber_id: 'EQP04-PM2',
  sensor_ids: ['ET_REFL'],
  lot_id: 'LOT004',
  wafer_nos: [2, 4, 6],
})
const finalIncidentTrace = await searchTraces(finalTraceScope)
assert.equal(finalIncidentTrace.total, 3)
assert.equal(finalIncidentTrace.wafers.every((item) => item.points.length === 6), true)
assert.equal(finalIncidentTrace.wafers[0].points[0].value, 37.467)
assert.equal(finalIncidentTrace.limits.ET_REFL.unit, 'W')
assert.equal(finalIncidentTrace.limits.ET_REFL.spec_upper, 30)
assert.equal('latency_ms' in runDetail.tools[0], false)

const pending = approvalViewState(
  { phase: 'idle', status: 'PENDING', error: null },
  { type: 'SUBMIT' },
)
assert.equal(pending.phase, 'pending')
assert.equal(approvalViewState(pending, { type: 'SUBMIT' }), pending)
assert.deepEqual(approvalViewState(pending, { type: 'SUCCESS', status: 'APPROVED' }), {
  phase: 'success',
  status: 'APPROVED',
  error: null,
})
assert.deepEqual(approvalViewState(pending, { type: 'CONFLICT' }), {
  phase: 'conflict',
  status: 'PENDING',
  error: null,
})
assert.deepEqual(approvalViewState(pending, { type: 'FAILURE', message: '503' }), {
  phase: 'error',
  status: 'PENDING',
  error: '503',
})

const agentRunPageSource = readFileSync(
  new URL('../src/features/agent/pages/AgentRunPage.jsx', import.meta.url),
  'utf8',
)
const approvalCall = agentRunPageSource.indexOf('    decideApprovalCanonical(')
const approvalErrorBranch = agentRunPageSource.indexOf('\n      (error) => {', approvalCall)
assert.ok(approvalCall >= 0 && approvalErrorBranch > approvalCall)
assert.match(agentRunPageSource.slice(approvalCall, approvalErrorBranch), /\n        load\(\)\n/)
assert.doesNotMatch(agentRunPageSource, /DOCUMENT'\)\?\.excerpt/)
assert.match(agentRunPageSource, /window\.setTimeout/)
assert.doesNotMatch(agentRunPageSource, /retryDelivery|deliveries\/.*retry/)
assert.match(agentRunPageSource, /<AlarmTracePanel/)
assert.match(agentRunPageSource, /alarmTrendScope\(alarm\)/, 'Agent도 화면 2와 같은 LOT 전체 단일 파라미터 조회를 사용해야 합니다')
assert.match(agentRunPageSource, /allowWaferSelection/, 'Agent trace도 화면 2와 같은 wafer 선택 패널을 사용해야 합니다')
assert.doesNotMatch(agentRunPageSource, /incidentTraceScope/, 'Agent 화면에서 여러 센서·여러 seq 비교 모드로 되돌리면 안 됩니다')
assert.ok(
  agentRunPageSource.indexOf('<AlarmTracePanel') < agentRunPageSource.indexOf('<AgentExecutionFlow'),
  'Agent 상세는 기준 알람 실측을 먼저 보여 준 뒤 실행 흐름을 설명해야 합니다',
)

const executionFlowSource = readFileSync(
  new URL('../src/features/agent/components/AgentExecutionFlow.jsx', import.meta.url),
  'utf8',
)
assert.match(executionFlowSource, /tool\.result_summary/)
assert.match(executionFlowSource, /개별 latency 미제공/)
for (const label of ['측정 데이터 근거', '매뉴얼 문서 근거', '설비 관계 근거', '분석 근거 조회']) {
  assert.match(executionFlowSource, new RegExp(label), '실행 흐름의 근거 수집 단계는 사용자 언어로 설명해야 합니다')
}
assert.match(executionFlowSource, /측정 데이터와 FDC 판정 근거를 확보했습니다/)
assert.match(executionFlowSource, /관련 매뉴얼 문서 근거를 확보했습니다/)
assert.match(executionFlowSource, /설비와 공정의 연결 관계 근거를 확보했습니다/)
assert.match(executionFlowSource, /item\.type === 'GRAPH'/)
assert.match(executionFlowSource, /function GraphEvidenceList/)
assert.match(executionFlowSource, /연결 \{items\.length\}건 확인/)
assert.match(executionFlowSource, /getChamberRelationsCore\(detail\.chamber_id\)/)
assert.match(executionFlowSource, /orientOntologyRelationships\(graph, layout\)/)
assert.match(executionFlowSource, /확인 관계 \{index \+ 1\}/)
assert.match(executionFlowSource, /relation\.source/)
assert.match(executionFlowSource, /relation\.label/)
assert.match(executionFlowSource, /relation\.target/)
assert.match(executionFlowSource, /연결 \{index \+ 1\} 직접 보기/)
assert.match(executionFlowSource, /data-result-summary=\{tool\.result_summary\}/, '영문 시스템 결과는 화면 문장 대신 비가시 진단값으로만 보존해야 합니다')
assert.match(executionFlowSource, /step\.value\.decided_at/)
assert.match(executionFlowSource, /ACTION-POLICY-V1 규칙 판정/)
assert.match(executionFlowSource, /data-testid="agent-diagnosis-five-blocks"/)
for (const label of ['종합 진단', '근거 충분성', '영향 범위', '유사 incident', '조치 후 관찰']) {
  assert.match(executionFlowSource, new RegExp(label))
}
assert.match(executionFlowSource, /const FLOW_LAYOUT/, '실행 흐름은 위에서 아래로 읽는 명시적 다이어그램 레이아웃이어야 합니다')
assert.match(executionFlowSource, /function DecisionNode/, '근거 충분성과 조치 판정은 의사결정 노드로 구분해야 합니다')
assert.match(executionFlowSource, /function ToolPlanNode/, '현재 입력과 C-7.1 재호출은 Tool 노드의 서로 다른 handle로 연결해야 합니다')
assert.match(executionFlowSource, /targetHandle: 'left'/, 'C-7.1 재호출 점선이 입력 알람 노드를 침범하면 안 됩니다')
assert.match(executionFlowSource, /function ApprovalStepNode/, '승인 노드는 전달 노드로 향하는 전용 오른쪽 출구가 필요합니다')
assert.match(executionFlowSource, /function DeliveryStepNode/, '전달 노드는 직접 조치와 승인 후 조치의 진입점을 분리해야 합니다')
assert.match(executionFlowSource, /승인 후 전달/, '승인 경로와 직접 전달 경로를 화면에서 구분해야 합니다')
assert.match(executionFlowSource, /근거 일부 부족 · 추가 수집/, '근거 부족 시 C-7.1 Tool 재선택 루프를 표시해야 합니다')
assert.match(executionFlowSource, /detail\.status === 'WAITING_APPROVAL'/, '기본 선택은 실제 Agent 실행 상태를 따라야 합니다')
assert.match(executionFlowSource, /detail\.status === 'COMPLETED'/, '완료 실행은 최종 기록 단계를 기본 선택해야 합니다')
assert.match(executionFlowSource, /C-7\.1 · EXPERIMENT/, '실험 확장을 현재 운영 실행과 명확히 구분해야 합니다')
assert.match(executionFlowSource, /data-testid="agent-execution-flow-launcher"/, '실행 흐름은 요약 패널을 눌러 크게 열 수 있어야 합니다')
assert.match(executionFlowSource, /role="dialog" aria-modal="true"/, '확대 흐름은 독립 상세 대화상자로 열려야 합니다')
assert.match(executionFlowSource, /h-\[calc\(100vh-48px\)\]/, '노드 선택과 무관하게 확대 흐름 높이를 고정해야 합니다')
assert.match(executionFlowSource, /data-testid="agent-execution-step-panel"/, '노드를 눌렀을 때만 큰 단계별 근거 패널을 표시해야 합니다')
assert.match(executionFlowSource, /onPaneClick=\{\(\) => setDetailOpen\(false\)\}/, '관계도 빈 영역을 누르면 근거 패널이 닫혀야 합니다')
assert.match(executionFlowSource, /nodeId === selectedId \? !open : true/, '같은 노드를 다시 누르면 근거 패널이 토글되어야 합니다')
assert.match(executionFlowSource, /agent-step-panel max-h-\[calc\(100vh-150px\)\] overflow-y-auto/, '근거 패널은 내용 높이를 사용하고 긴 내용만 내부 스크롤되어야 합니다')
assert.match(executionFlowSource, /function AlarmPreview/, '입력 노드에서 실제 대표 알람 정보를 바로 확인할 수 있어야 합니다')
assert.match(executionFlowSource, /function AuditPreview/, '감사 노드에서 현재 실행의 실제 이벤트를 바로 확인할 수 있어야 합니다')
assert.doesNotMatch(executionFlowSource, /function StepNavigation/, '노드 패널을 큰 이동 버튼만 있는 빈 화면으로 만들면 안 됩니다')
assert.doesNotMatch(executionFlowSource, /<div className="h-\[300px\]/, 'React Flow 캔버스를 고정 높이로 남기면 안 됩니다')
assert.match(executionFlowSource, /proOptions=\{\{ hideAttribution: true \}\}/, 'React Flow 브랜딩 링크를 노출하지 않아야 합니다')

assert.doesNotMatch(agentRunPageSource, /<RunContextAsk/, '시연 핵심 흐름을 흐리는 자유형 후속 질문 UI는 Agent 상세에 노출하지 않습니다')
assert.doesNotMatch(executionFlowSource, /<Background/, '복잡해 보이는 점 배경을 사용하지 않아야 합니다')
assert.doesNotMatch(executionFlowSource, /index \* 172/, '11개 노드를 한 줄 너비로 펼쳐 축소하면 안 됩니다')
assert.match(executionFlowSource, /detail\.chamber_id \?\? alarm\?\.chamber_id/)
assert.match(executionFlowSource, /detail\.lot_id \?\? alarm\?\.lot_id/)
assert.match(executionFlowSource, /\(\{incidentScopeLabel\} 기준\)/)

const runContextAskSource = readFileSync(
  new URL('../src/features/agent/components/RunContextAsk.jsx', import.meta.url),
  'utf8',
)
assert.match(runContextAskSource, /askAgent\(\{ question: normalized, agent_run_id: agentRunId \}\)/)
assert.match(runContextAskSource, /data-testid="agent-run-context-ask"/)

const traceChartSource = readFileSync(
  new URL('../src/shared/components/trace/TraceChart.jsx', import.meta.url),
  'utf8',
)
assert.match(traceChartSource, /connectNulls=\{false\}/)
assert.match(traceChartSource, /syncId=\{syncId\}/)
assert.match(traceChartSource, /seq_no/)
assert.match(traceChartSource, /sensor_name/)
assert.match(traceChartSource, /limitDifference/)
assert.match(traceChartSource, /<Brush/)
assert.match(traceChartSource, /domain=\{yDomain\}/)
assert.match(traceChartSource, /type="linear"/)
assert.doesNotMatch(traceChartSource, /type="monotone"/)
assert.match(traceChartSource, /selectedView \? '측정 시각' : 'WAFER'/)
assert.match(traceChartSource, /selectedWaferChartModel\(wafers\[0\]\)/)

const historyTrendSource = readFileSync(
  new URL('../src/shared/components/trace/HistoryTrendChart.jsx', import.meta.url),
  'utf8',
)
assert.match(historyTrendSource, /const bodyHeight = allowWaferSelection \? 'h-\[500px\]' : 'h-\[300px\]'/)
assert.match(historyTrendSource, /viewMode === 'selected' \? 'h-full min-h-\[500px\]' : 'h-\[300px\]'/)

const runHeaderSource = readFileSync(
  new URL('../src/features/agent/components/RunHeaderCard.jsx', import.meta.url),
  'utf8',
)
const runListSource = readFileSync(
  new URL('../src/features/agent/components/RunListPanel.jsx', import.meta.url),
  'utf8',
)
const runSummarySource = readFileSync(
  new URL('../src/features/agent/components/RunSummaryCard.jsx', import.meta.url),
  'utf8',
)
const impactModalSource = readFileSync(
  new URL('../src/features/agent/components/AgentImpactGraphModal.jsx', import.meta.url),
  'utf8',
)
assert.match(runHeaderSource, /runStatusVariant\(run\.status\)/)
assert.match(runHeaderSource, /alarmDisplayLabel\(\{/)
assert.doesNotMatch(runHeaderSource, /\{alarmId\}/, 'R03 내부 hash ID를 헤더 제목에 직접 렌더링하면 안 됩니다')
assert.match(runHeaderSource, /data-agent-run-id=\{run\.agent_run_id\}/, 'run ID는 화면 제목이 아니라 진단 속성으로만 남겨야 합니다')
assert.match(runHeaderSource, /title=\{`실행 ID: \$\{run\.agent_run_id\}`\}/)
assert.match(runListSource, /runStatusVariant\(r\.status\)/)
assert.match(runSummarySource, /alarmJudgement\(run, repAlarm\)/)
assert.match(runSummarySource, /detail\?\.prediction\?\.cause_summary/, '알람 요약은 저장된 LLM 원인 분석을 우선 사용해야 합니다')
assert.match(runSummarySource, /item\.kind === 'WAFER'/, '알람 요약은 incident 영향 범위의 발생 WAFER를 표시해야 합니다')
assert.match(runSummarySource, /impactSourceOf\(\{ kind: 'WAFER', source_id: wafer \}\)/, 'LLM 요약 WAFER도 영향 범위와 같은 W번호 형식이어야 합니다')
assert.match(runSummarySource, /SummaryFact label="대상 LOT"/, 'LLM 요약 LOT 라벨은 영향 범위와 같아야 합니다')
assert.match(runSummarySource, /Agent 분석 요약/)
assert.match(runSummarySource, /LLM 원인 분석/)
assert.match(runSummarySource, /data-testid="agent-analysis-decision-summary"/)
assert.match(runSummarySource, /grid grid-cols-2 gap-3/, 'Agent 분석 4블록은 두 열·두 줄로 배치해야 합니다')
assert.match(runSummarySource, /영향 범위 보기/)
assert.match(runSummarySource, /AgentImpactGraphModal/)
assert.match(runSummarySource, /RepresentativeAlarmModal/)
assert.match(runSummarySource, /HistoryTrendChart/)
assert.match(runSummarySource, /대표 알람 보기/)
assert.match(impactModalSource, /getChamberRelationsCore\(selection\.chamberId\)/)
assert.match(impactModalSource, /getAllAlarms\(params\)/)
assert.match(impactModalSource, /graph\.graph_revision !== selection\.graphRevision/)
assert.match(impactModalSource, /impactNodeIds=\{directNodeIds\}/)
assert.match(impactModalSource, /checkRequiredNodeIds=\{checkNodeIds\}/)
assert.match(impactModalSource, /selectedNodeId=\{selectedNode\?\.id \?\? null\}/)
assert.match(impactModalSource, /data-testid="agent-impact-node-panel"/)
for (const label of ['Agent 판단 연결', '공개 속성', '선택 노드 운영 요약']) {
  assert.match(impactModalSource, new RegExp(label), `영향 범위 노드 패널에 ${label}이 필요합니다`)
}
for (const label of ['영향 범위', '권고 조치', '승인 · 전달 · 관찰']) {
  assert.match(runSummarySource, new RegExp(label), `Agent 분석 요약에 ${label} 정보를 함께 제공해야 합니다`)
}
assert.doesNotMatch(runSummarySource, /값이 \$\{bound\}을 벗어나는/, '알람 요약을 고정 템플릿 문장으로 조립하면 안 됩니다')
assert.doesNotMatch(runSummarySource, /recommended_action === 'MONITORING' \? 'OOC' : 'OOS'/)

assert.match(executionFlowSource, /\/audit-logs\?entity_id=/, '감사 기록 노드는 실행 ID로 감사로그 상세에 연결해야 합니다')
const auditPageSource = readFileSync(
  new URL('../src/features/analytics/pages/AuditLogPage.jsx', import.meta.url),
  'utf8',
)
assert.match(auditPageSource, /searchParams\.get\('entity_id'\)/, '감사로그 화면은 실행 흐름의 entity_id deep-link를 적용해야 합니다')

assert.deepEqual(
  auditTargetsOf({ agent_run_id: 'RUN-1', action_id: 'ACT-1', approval_id: null }),
  [
    ['AGENT_RUN', 'RUN-1'],
    ['ACTION', 'ACT-1'],
  ],
)
assert.deepEqual(
  mergeAuditItems([
    [{ audit_id: 1, occurred_at: '2026-08-29T12:00:00+09:00' }],
    [
      { audit_id: 1, occurred_at: '2026-08-29T12:00:00+09:00' },
      { audit_id: 2, occurred_at: '2026-08-29T13:00:00+09:00' },
    ],
  ]).map((item) => item.audit_id),
  [2, 1],
)

const runAuditLogs = await getAuditLogsCore({ entity_type: 'AGENT_RUN', entity_id: 'RUN-000001' })
const actionAuditLogs = await getAuditLogsCore({ entity_type: 'ACTION', entity_id: 'ACT-000003' })
const approvalAuditLogs = await getAuditLogsCore({ entity_type: 'APPROVAL', entity_id: 'APR-000001' })
assert.deepEqual(runAuditLogs.map((item) => item.event_type), ['AGENT_RUN_STARTED', 'HYPOTHESIS_GENERATED'])
assert.equal(actionAuditLogs[0].after.channel, 'EMAIL')
assert.equal(actionAuditLogs[0].after.transport, 'N8N_WEBHOOK')
assert.equal(approvalAuditLogs[0].after.status, 'PENDING')
assert.equal([...runAuditLogs, ...actionAuditLogs, ...approvalAuditLogs].some((item) => item.after?.channel === 'MES_MOCK'), false)

const sentPage = await getActions({ send_status: 'SENT', page: 1, size: 100 })
assert.ok(sentPage.items.length > 0)
assert.ok(sentPage.items.every((item) => hasDeliveryStatus(item, 'SENT')))
assert.ok(sentPage.items.every((item) => !('send_status' in item) && !('send_channel' in item)))

const html = renderToStaticMarkup(
  React.createElement(
    'section',
    { 'data-phase': 'success' },
    React.createElement('span', null, 'RUN-NULL-FAULT'),
    React.createElement('span', null, null ?? '—'),
  ),
)
assert.match(html, /RUN-NULL-FAULT/)
assert.match(html, /—/)
assert.doesNotMatch(html, /ground_truth|hidden_gold|DEFAULT_RUN_ID/i)

console.log('OK agent screen: canonical deliveries · approval state · deep-link · SSR')

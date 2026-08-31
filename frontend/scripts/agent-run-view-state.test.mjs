import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { getActions, getRun, getRunsCore } from '../src/shared/api/agent.js'
import { getAlarm } from '../src/shared/api/detection.js'
import { getChamberRelationsCore, getDocument } from '../src/shared/api/knowledge.js'
import { matchTab, sortActions } from '../src/features/agent/actionsSort.js'
import {
  actionSeverity,
  adaptActionForLegacyPage,
  alarmJudgement,
  approvalViewState,
  evidenceHref,
  hasDeliveryStatus,
  measuredText,
  selectInitialRun,
  trendUnavailableMessage,
} from '../src/features/agent/agent-run-view-state.js'
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
assert.equal(runStatusVariant('RUNNING'), 'bg-blue')
assert.equal(runStatusVariant('WAITING_APPROVAL'), 'bg-amber')
assert.equal(runStatusVariant('COMPLETED'), 'bg-green')
assert.equal(runStatusVariant('FAILED'), 'bg-red')
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

const runs = [
  { agent_run_id: 'RUN-2', status: 'COMPLETED' },
  { agent_run_id: 'RUN-1', status: 'WAITING_APPROVAL' },
]
assert.equal(selectInitialRun(runs).agent_run_id, 'RUN-1')
assert.equal(selectInitialRun([]), null)
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
  '/documents?document_id=DOC+1&chunk_id=DOC+1%3Acs1%3A0001',
)
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

const linkedDocument = await getDocument('DOC-TROUBLE-FDC')
assert.ok(linkedDocument.chunks.some((chunk) => chunk.chunk_id === 'DOC-TROUBLE-FDC:cs2:0006'))

const linkedAlarm = await getAlarm('R03-f41e6518529e8ed5e6a9', 'R03')
assert.equal(linkedAlarm.alarm_id, 'R03-f41e6518529e8ed5e6a9')
assert.equal(linkedAlarm.source, 'R03')
assert.equal(linkedAlarm.latest_agent_run_id, 'RUN-000001')
assert.equal(await getAlarm('R03-f41e6518529e8ed5e6a9', 'TRACE'), null)

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
assert.match(runHeaderSource, /runStatusVariant\(run\.status\)/)
assert.match(runListSource, /runStatusVariant\(r\.status\)/)
assert.match(runSummarySource, /alarmJudgement\(run, repAlarm\)/)
assert.doesNotMatch(runSummarySource, /recommended_action === 'MONITORING' \? 'OOC' : 'OOS'/)

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

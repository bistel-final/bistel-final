import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'
import { deliveryStatusMeta } from '../src/features/agent/delivery-flow-state.js'
import { deliveryStatusSummary } from '../src/features/agent/components/agentModel.js'
import { deliveryFlowEdges, isNotificationAction, MOCK_NOTIFY_POLICY } from '../src/features/agent/notification-state.js'

const action = {
  action_id: 'ACT-test', action_code: 'EQP_HOLD', delivery_policy: MOCK_NOTIFY_POLICY,
  approval_status: null, deliveries: [{ channel: 'EMAIL', status: 'SENT' }, { channel: 'MES', status: 'SENT' }],
}
const before = JSON.stringify(action)
assert.equal(isNotificationAction(action), true)
assert.equal(isNotificationAction({ action_code: 'EQP_HOLD' }), false)
assert.equal(deliveryStatusMeta('SENT', 'MES').label, '모의 응답 확인')
assert.equal(deliveryStatusMeta('SENT', 'EMAIL').label, '전송 완료')
for (const status of ['WAITING', 'SENDING', 'FAILED', 'UNKNOWN', 'CANCELED', 'BLOCKED', undefined]) {
  assert.notEqual(deliveryStatusMeta(status, 'MES').label, '모의 응답 확인')
}
assert.match(deliveryStatusSummary(action), /MES Mock 모의 응답 확인/)
const edges = [
  { id: 'action-approval', source: 'action', target: 'approval' },
  { id: 'approval-delivery', source: 'approval', target: 'delivery' },
  { id: 'action-delivery', source: 'action', target: 'delivery', label: 'legacy' },
]
assert.deepEqual(deliveryFlowEdges(edges, action), [{ id: 'action-delivery', source: 'action', target: 'delivery', label: '자동 알림 · 모의 연동' }])
assert.equal(deliveryFlowEdges(edges, {}), edges)
assert.equal(edges[2].label, 'legacy')
const server = await createServer({ server: { middlewareMode: true, hmr: false, ws: false }, appType: 'custom' })
try {
  const { default: Flow } = await server.ssrLoadModule('/src/features/agent/components/DeliveryFlow.jsx')
  for (const compact of [false, true]) {
    const html = renderToStaticMarkup(React.createElement(Flow, { action, compact }))
    for (const label of ['Kafka', 'MES Mock', '모의 응답 확인', '열람·확인 여부는 수집하지 않습니다', '실제 설비 정지']) assert.ok(html.includes(label))
    assert.doesNotMatch(html, /사람 승인|승인 대기|확인 완료|<button|notification-confirm/)
    const pending = renderToStaticMarkup(React.createElement(Flow, {
      action: { ...action, deliveries: [{ channel: 'EMAIL', status: 'SENT' }, { channel: 'MES', status: 'WAITING' }] },
    }))
    assert.doesNotMatch(pending, /모의 응답 확인|승인 전 Kafka 미발행/)
  }
  const legacy = renderToStaticMarkup(React.createElement(Flow, { action: { ...action, delivery_policy: 'ACTION-POLICY-V1', approval_status: 'PENDING' } }))
  assert.match(legacy, /승인 전 Kafka 미발행/)
  const rejected = renderToStaticMarkup(React.createElement(Flow, { action: { ...action, delivery_policy: 'ACTION-POLICY-V1', approval_status: 'REJECTED' } }))
  assert.match(rejected, /Kafka·MES 전송 없이/)
} finally { await server.close() }
assert.equal(JSON.stringify(action), before)
const routes = readFileSync(new URL('../src/app/routes.jsx', import.meta.url), 'utf8')
assert.doesNotMatch(routes, /notification-confirm|NotificationConfirmPage/)
const detail = readFileSync(new URL('../src/features/agent/components/RunDetailModal.jsx', import.meta.url), 'utf8')
assert.doesNotMatch(detail, /설비 투입 중단|HOLD 해제|점검 완료 후|알림 확인:|확인 완료 버튼/)
assert.match(detail, /notification \? \(/)
const flow = readFileSync(new URL('../src/features/agent/components/AgentExecutionFlow.jsx', import.meta.url), 'utf8')
assert.match(flow, /deliveryFlowEdges\(FLOW_EDGES, detail.action\)/)
assert.match(flow, /step.id !== 'approval' \|\| !isNotificationAction\(detail.action\)/)
console.log('OK mock-notify: automatic route, status-only results, legacy history preserved, no confirmation tracking')

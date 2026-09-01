// Mock core 응답을 API v3 component key에서 직접 파생해 대조한다.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import {
  getAction,
  getActionsCore,
  getApprovalsCore,
  getRun,
  getRunsCore,
} from '../src/shared/api/agent.js'
import { getAlarmsCore } from '../src/shared/api/detection.js'

const spec = JSON.parse(readFileSync(new URL('../../docs/deliverables/api/api_spec_v3.json', import.meta.url)))

const assertComponent = (label, value, componentName) => {
  const component = spec.components[componentName]
  assert.ok(component, `${componentName} component가 없다`)
  assert.deepEqual(
    Object.keys(value).sort(),
    Object.keys(component.fields).sort(),
    `${label} key가 API v3 ${componentName}과 다르다`,
  )
}

const alarm = (await getAlarmsCore())[0]
const run = (await getRunsCore())[0]
const runDetail = await getRun(run.agent_run_id)
const actions = await getActionsCore()
const action = actions[0]
const deliveredAction = actions.find((item) => item.deliveries.length > 0)
const actionDetail = await getAction(action.action_id)
const deliveredActionDetail = await getAction(deliveredAction.action_id)
const approval = (await getApprovalsCore())[0]

assertComponent('GET /alarms item', alarm, 'AlarmItem')
assertComponent('GET /agent/runs item', run, 'PublicAgentRunItem')
assertComponent('GET /agent/runs/{run_id}', runDetail, 'AgentRunDetailResponse')
assertComponent('GET /actions item', action, 'ActionItem')
assertComponent('GET /actions/{action_id}', actionDetail, 'ActionDetailResponse')
assertComponent('GET /approvals item', approval, 'PublicApprovalItem')
assertComponent('run detail prediction', runDetail.prediction, 'AgentPredictionDetailItem')
assertComponent('run detail action delivery', runDetail.action.deliveries[0], 'ActionDeliveryDetailItem')
assertComponent('action detail delivery', deliveredActionDetail.deliveries[0], 'ActionDeliveryDetailItem')

const serialized = JSON.stringify({ run, runDetail, action, actionDetail })
for (const forbidden of ['request_hash', 'provider_message_id', 'last_error', 'raw_prompt', 'raw_response', 'HIDDEN_GOLD']) {
  assert.doesNotMatch(serialized, new RegExp(forbidden, 'i'))
}

console.log('OK api-schema: API v3 component 파생 core mock 계약')

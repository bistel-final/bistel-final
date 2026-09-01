// live ActionItem 정렬·탭 규칙을 API v3 fixture로 검증한다.
import assert from 'node:assert/strict'
import { matchTab, sortActions, tabParams } from '../src/features/agent/actionsSort.js'
import { PUBLIC_ACTIONS } from '../src/features/agent/mock/publicActions.js'

const expected = ['ACT-0005', 'ACT-0003', 'ACT-0001']
assert.deepEqual(sortActions([...PUBLIC_ACTIONS].reverse()).map((item) => item.action_id), expected)
assert.deepEqual(tabParams('PENDING'), { approval_status: 'PENDING' })
assert.deepEqual(tabParams('SENT'), { send_status: 'SENT' })
assert.deepEqual(tabParams('ALL'), {})
assert.equal(PUBLIC_ACTIONS.filter((item) => matchTab(item, 'PENDING')).length, 1)
assert.equal(PUBLIC_ACTIONS.filter((item) => matchTab(item, 'SENT')).length, 2)
assert.equal(PUBLIC_ACTIONS.filter((item) => matchTab(item, 'WAITING')).length, 1)
assert.ok(PUBLIC_ACTIONS.every((item) => !('send_status' in item) && !('send_channel' in item)))
assert.ok(PUBLIC_ACTIONS.every((item) => Array.isArray(item.deliveries)))

console.log(`OK actions-sort: API v3 ActionItem ${PUBLIC_ACTIONS.length}건`)

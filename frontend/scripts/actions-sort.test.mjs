// 조치 목록 정렬·탭 검증 — node scripts/actions-sort.test.mjs
// fixture ACTIONS를 셔플한 뒤 화면과 동일한 sortActions(공용 모듈)를 적용해
// 기대 순서(PENDING 최상단 → created_at 내림차순)와 일치하는지 확인한다.
// 필드명은 명세 ActionDetailResponse 기준(send_channel · 최상위 sensor_id/alarm_ids/alarm_count).
import assert from 'node:assert/strict'
import { ACTIONS, ACTION_TABS } from '../src/features/agent/mock/actions.js'
import { matchTab, sortActions, tabParams } from '../src/features/agent/actionsSort.js'
import { toIso } from '../src/shared/api/format.js'

// 디자인 v2 05_조치목록 rows 순서 그대로 (전체 탭 기준)
const EXPECTED = [
  'ACT-0010', 'ACT-0005', // PENDING 최상단 (그 안에서 시각 내림차순)
  'ACT-0008', 'ACT-0009', 'ACT-0006', 'ACT-0007', 'ACT-0004', 'ACT-0003', 'ACT-0002', 'ACT-0001',
]

// 결정적 셔플 (mulberry32 + Fisher–Yates) — 재현 가능한 실패를 위해 시드 고정
const rng = (seed) => () => {
  seed = (seed + 0x6d2b79f5) | 0
  let t = Math.imul(seed ^ (seed >>> 15), 1 | seed)
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296
}
const shuffle = (arr, seed) => {
  const a = [...arr]
  const rand = rng(seed)
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1))
    ;[a[i], a[j]] = [a[j], a[i]]
  }
  return a
}

assert.equal(ACTIONS.length, EXPECTED.length, `fixture는 ${EXPECTED.length}건이어야 한다`)

// 필드명 회귀 방지 — 옛 축약 필드(channel · incident.sensor_id)로 되돌아가면 여기서 깨진다
for (const a of ACTIONS) {
  assert.ok('send_channel' in a, `${a.action_id}: send_channel 이 없다 (channel 금지)`)
  assert.ok(!('channel' in a), `${a.action_id}: 옛 channel 필드가 남아 있다`)
  assert.equal(typeof a.sensor_id, 'string', `${a.action_id}: sensor_id 는 최상위 필드다`)
  assert.deepEqual(Object.keys(a.incident).sort(), ['chamber_id', 'lot_id'], `${a.action_id}: incident 키 불일치`)
  assert.ok(Array.isArray(a.alarm_ids), `${a.action_id}: alarm_ids 는 최상위 배열이다`)
  assert.equal(a.alarm_count, a.alarm_ids.length, `${a.action_id}: alarm_count 가 alarm_ids 수와 다르다`)
}

// 여러 시드로 셔플해도 항상 기대 순서로 복원되는지 — raw fixture 형태
for (const seed of [1, 7, 42, 20260604]) {
  const ids = sortActions(shuffle(ACTIONS, seed)).map((a) => a.action_id)
  assert.deepEqual(ids, EXPECTED, `seed=${seed} raw 정렬 순서 불일치`)
}

// 화면 실제 입력 형태(getActions → created_at ISO 변환) 기준으로도 동일해야 한다
const isoItems = ACTIONS.map((a) => ({ ...a, created_at: toIso(a.created_at) }))
for (const seed of [3, 99]) {
  const ids = sortActions(shuffle(isoItems, seed)).map((a) => a.action_id)
  assert.deepEqual(ids, EXPECTED, `seed=${seed} ISO 정렬 순서 불일치`)
}

// 불변식: PENDING 블록이 항상 최상단이고, 각 블록 내부는 created_at 내림차순
const sorted = sortActions(shuffle(ACTIONS, 5))
const firstNonPending = sorted.findIndex((a) => a.approval_status !== 'PENDING')
assert.ok(
  sorted.slice(firstNonPending).every((a) => a.approval_status !== 'PENDING'),
  'PENDING 행이 최상단에 몰려 있어야 한다',
)
for (let i = 1; i < firstNonPending; i++)
  assert.ok(sorted[i - 1].created_at >= sorted[i].created_at, 'PENDING 블록 내 시각 내림차순 위반')
for (let i = firstNonPending + 1; i < sorted.length; i++)
  assert.ok(sorted[i - 1].created_at >= sorted[i].created_at, '나머지 블록 내 시각 내림차순 위반')

// 원본 불변 확인 — sortActions는 입력 배열을 변경하지 않는다
const before = ACTIONS.map((a) => a.action_id).join(',')
sortActions(ACTIONS)
assert.equal(ACTIONS.map((a) => a.action_id).join(','), before, '입력 배열이 변경되면 안 된다')

// 탭 → 서버 파라미터 매핑 (승인 대기만 approval_status, 나머지는 send_status, 전체는 없음)
assert.deepEqual(tabParams('PENDING'), { approval_status: 'PENDING' })
assert.deepEqual(tabParams('SENT'), { send_status: 'SENT' })
assert.deepEqual(tabParams('FAILED'), { send_status: 'FAILED' })
assert.deepEqual(tabParams('SENDING'), { send_status: 'SENDING' })
assert.deepEqual(tabParams('ALL'), {})

// 탭 배지 건수 — 승인 대기 2 · 전송 실패 0 · 진행 중 0 · 완료 8 · 전체 10
const EXPECTED_COUNTS = { PENDING: 2, FAILED: 0, SENDING: 0, SENT: 8, ALL: 10 }
const counts = Object.fromEntries(
  ACTION_TABS.map((t) => [t.key, ACTIONS.filter((a) => matchTab(a, t.key)).length]),
)
assert.deepEqual(counts, EXPECTED_COUNTS, '탭 건수 불일치')

console.log(`OK actions-sort: ${EXPECTED.length}건 · 셔플 6회 → 기대 순서 일치`)
console.log(`   ${EXPECTED.join(' → ')}`)
console.log(`OK actions-tabs: ${ACTION_TABS.map((t) => `${t.label} ${counts[t.key]}`).join(' · ')}`)

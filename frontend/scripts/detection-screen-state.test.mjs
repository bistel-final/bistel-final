import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import {
  hasDashboardResults,
  partitionAlarms,
  periodLabel,
  runErrorMessage,
} from '../src/features/detection/detection-screen-state.js'

assert.equal(periodLabel({ from: '', to: '' }), '전체 기간')
assert.equal(periodLabel({ from: '2026-06-01', to: '2026-06-04' }), '2026-06-01 ~ 2026-06-04')
assert.equal(periodLabel({ from: '2026-06-01', to: '' }), '2026-06-01 이후')
assert.equal(periodLabel({ from: '', to: '2026-06-04' }), '2026-06-04 이전')

assert.equal(runErrorMessage({ response: { status: 409, data: { detail: '이미 실행 중' } } }), '이미 실행 중')
assert.equal(runErrorMessage({ response: { status: 409, data: { detail: [] } } }), '이 incident는 이미 분석이 진행 중입니다.')
assert.equal(runErrorMessage({ response: { status: 422, data: { detail: [{ loc: ['body'] }] } } }), '분석을 실행할 수 없는 알람입니다.')
assert.equal(runErrorMessage({ response: { status: 503, data: {} } }), 'Agent 실행 서비스를 사용할 수 없습니다.')
assert.equal(runErrorMessage(new Error('network')), '분석 실행 요청에 실패했습니다.')

const alarms = [
  { alarm_id: 'TRACE-1', source: 'TRACE', occurred_at: '2026-06-01T00:00:00+09:00' },
  { alarm_id: 'R03-1', source: 'R03', occurred_at: '2026-06-03T00:00:00+09:00' },
  { alarm_id: 'SUMMARY-1', source: 'SUMMARY', occurred_at: '2026-06-02T00:00:00+09:00' },
]
const partitioned = partitionAlarms(alarms)
assert.deepEqual(partitioned.trace.map((alarm) => alarm.alarm_id), ['TRACE-1'])
assert.deepEqual(partitioned.summary.map((alarm) => alarm.alarm_id), ['SUMMARY-1'])
assert.deepEqual(partitioned.r03.map((alarm) => alarm.alarm_id), ['R03-1'])
assert.deepEqual(alarms.map((alarm) => alarm.alarm_id), ['TRACE-1', 'R03-1', 'SUMMARY-1'], '입력 목록을 변경하면 안 됩니다')

assert.equal(hasDashboardResults(null), false)
assert.equal(hasDashboardResults({ total: 0 }), false)
assert.equal(hasDashboardResults({ total: 1 }), true)

const alarmsPageSource = await readFile(new URL('../src/features/detection/pages/AlarmsPage.jsx', import.meta.url), 'utf8')
const handlerStart = alarmsPageSource.indexOf('  const handleRunAnalysis = () => {')
const handlerEnd = alarmsPageSource.indexOf('\n  const rows = useMemo', handlerStart)
const runHandler = alarmsPageSource.slice(handlerStart, handlerEnd)
assert.ok(handlerStart >= 0 && handlerEnd > handlerStart)
assert.equal((runHandler.match(/createRun\(/g) ?? []).length, 1, '분석 버튼 클릭당 POST 경로는 하나여야 합니다')
assert.match(runHandler, /navigate\(`\/agent-runs\/\$\{accepted\.agent_run_id\}`\)/, '202 응답 run 상세로 이동해야 합니다')
assert.match(runHandler, /catch\(\(error\) => setRunError\(runErrorMessage\(error\)\)\)/)
assert.doesNotMatch(runHandler, /retry|setTimeout|setInterval/, '409·422·503에서 자동 재시도하면 안 됩니다')

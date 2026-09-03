import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import {
  analysisActionOf,
  dataDateRange,
  hasDashboardResults,
  partitionAlarms,
  periodLabel,
  runErrorMessage,
} from '../src/features/detection/detection-screen-state.js'
import { ALL, DEFAULT_SCOPE, scopeFromParams, scopeToParams } from '../src/features/detection/components/scopeModel.js'
import { getAlarm, searchTraces } from '../src/shared/api/detection.js'
import { alarmTrendScope } from '../src/shared/trace/incidentTrace.js'
import { formatMeasuredAt, selectedWaferChartModel } from '../src/shared/trace/traceModel.js'

// 기간 필터 기본값 — 대시보드·알람 히스토리가 같은 규칙으로 응답 범위를 채운다.
assert.equal(dataDateRange([]), null)
assert.equal(dataDateRange(null), null)
assert.deepEqual(
  dataDateRange([
    { occurred_at: '2026-06-03T10:00:00+09:00' },
    { occurred_at: '2026-06-01T23:17:23+09:00' },
    { occurred_at: '2026-06-04T02:00:00+09:00' },
  ]),
  { from: '2026-06-01', to: '2026-06-04' },
)
assert.equal(dataDateRange([{ occurred_at: null }]), null, '일자가 없는 응답은 기간을 강제하지 않아야 합니다')

assert.equal(periodLabel({ from: '', to: '' }), '전체 기간')
assert.equal(periodLabel({ from: '2026-06-01', to: '2026-06-04' }), '2026-06-01 ~ 2026-06-04')
assert.equal(periodLabel({ from: '2026-06-01', to: '' }), '2026-06-01 이후')
assert.equal(periodLabel({ from: '', to: '2026-06-04' }), '2026-06-04 이전')

assert.equal(runErrorMessage({ response: { status: 409, data: { detail: '이미 실행 중' } } }), '이미 실행 중')
assert.equal(runErrorMessage({ response: { status: 409, data: { message: '동일 incident가 이미 처리됐습니다.' } } }), '동일 incident가 이미 처리됐습니다.')
assert.equal(runErrorMessage({ response: { status: 409, data: { detail: [] } } }), '이 incident에는 기존 분석 실행이 있습니다.')
assert.equal(runErrorMessage({ response: { status: 422, data: { detail: [{ loc: ['body'] }] } } }), '분석을 실행할 수 없는 알람입니다.')
assert.equal(runErrorMessage({ response: { status: 503, data: {} } }), 'Agent 실행 서비스를 사용할 수 없습니다.')
assert.equal(runErrorMessage(new Error('network')), '분석 실행 요청에 실패했습니다.')

assert.deepEqual(analysisActionOf(null), { mode: 'CREATE', label: '분석 실행', runId: null })
assert.deepEqual(
  analysisActionOf({ latest_agent_run_id: 'RUN-1', agent_run_status: 'COMPLETED' }),
  { mode: 'OPEN', label: '분석 결과 보기', runId: 'RUN-1' },
)
assert.deepEqual(
  analysisActionOf({ latest_agent_run_id: 'RUN-2', agent_run_status: 'RUNNING' }),
  { mode: 'OPEN', label: '진행 중인 분석 보기', runId: 'RUN-2' },
)
assert.deepEqual(
  analysisActionOf({ latest_agent_run_id: 'RUN-3', agent_run_status: 'FAILED' }),
  { mode: 'OPEN', label: '실패 분석 보기', runId: 'RUN-3' },
)

const alarms = [
  { alarm_id: 'TRACE-1', source: 'TRACE', occurred_at: '2026-06-01T00:00:00+09:00' },
  { alarm_id: 'R03-1', source: 'R03', occurred_at: '2026-06-03T00:00:00+09:00' },
  { alarm_id: 'SUMMARY-1', source: 'SUMMARY', occurred_at: '2026-06-02T00:00:00+09:00' },
]
const partitioned = partitionAlarms(alarms)
assert.deepEqual(partitioned.all.map((alarm) => alarm.alarm_id), ['R03-1', 'SUMMARY-1', 'TRACE-1'])
assert.deepEqual(partitioned.trace.map((alarm) => alarm.alarm_id), ['TRACE-1'])
assert.deepEqual(partitioned.summary.map((alarm) => alarm.alarm_id), ['SUMMARY-1'])
assert.deepEqual(partitioned.r03.map((alarm) => alarm.alarm_id), ['R03-1'])
assert.deepEqual(alarms.map((alarm) => alarm.alarm_id), ['TRACE-1', 'R03-1', 'SUMMARY-1'], '입력 목록을 변경하면 안 됩니다')

assert.equal(hasDashboardResults(null), false)
assert.equal(hasDashboardResults({ total: 0 }), false)
assert.equal(hasDashboardResults({ total: 1 }), true)
assert.equal(formatMeasuredAt('2026-08-04T06:52:29+09:00'), '2026.08.04 06:52:29', '원본 timestamp는 한국 기준 연월일·시각으로 정리해야 합니다')
assert.equal(formatMeasuredAt('invalid'), null, '잘못된 timestamp는 화면에 노출하지 않아야 합니다')
assert.equal(
  selectedWaferChartModel({ wafer_no: 1, points: [{ measured_at: '2026-08-04T06:52:29+09:00', value: 1 }] }).rows[0].point_label,
  '06:52:29',
  'X축은 날짜를 반복하지 않고 시각만 유지해야 합니다',
)

assert.deepEqual(
  alarmTrendScope({ lot_id: 'LOT004', chamber_id: 'EQP04-PM2', sensor_id: 'ET_REFL', wafer_no: 2 }),
  { sensor_ids: ['ET_REFL'], lot_id: 'LOT004' },
  '알람 화면은 선택 파라미터의 LOT 전체 wafer를 선택할 수 있어야 합니다',
)
const finalAlarm = await getAlarm('TAL-0007', 'TRACE')
const finalTrend = await searchTraces(alarmTrendScope(finalAlarm))
assert.equal(finalTrend.total, 25, 'LOT004의 최종 데이터 기준 전체 25개 wafer를 선택할 수 있어야 합니다')
assert.deepEqual(finalTrend.wafers.map((item) => item.wafer_no), Array.from({ length: 25 }, (_, index) => index + 1))
assert.equal(finalTrend.wafers.filter((item) => item.chamber_id === 'EQP04-PM1').length, 13)
assert.equal(finalTrend.wafers.filter((item) => item.chamber_id === 'EQP04-PM2').length, 12)
assert.equal(finalTrend.wafers.every((item) => item.points.length === 6), true)
assert.equal(finalTrend.limits.ET_REFL.spec_upper, 30)

const alarmsPageSource = await readFile(new URL('../src/features/detection/pages/AlarmsPage.jsx', import.meta.url), 'utf8')
const dashboardPageSource = await readFile(new URL('../src/features/detection/pages/DashboardPage.jsx', import.meta.url), 'utf8')
// 대시보드 KPI → 알람 히스토리 이동은 지금 적용된 필터를 쿼리로 넘겨 같은 집계 범위를 연다.
assert.match(
  dashboardPageSource,
  /const alarmsPath = \(tab\) => `\/alarms\?\$\{scopeToParams\(applied, \{ tab \}\)\}`/,
  '대시보드 KPI 이동은 적용된 필터를 쿼리로 넘겨야 합니다',
)
assert.match(dashboardPageSource, /onTotal=\{\(\) => navigate\(alarmsPath\('ALL'\)\)\}/, '대시보드 전체 알람은 전체 히스토리로 이동해야 합니다')
assert.match(dashboardPageSource, /onOos=\{\(\) => navigate\(alarmsPath\('TRACE'\)\)\}/)
assert.match(dashboardPageSource, /onOoc=\{\(\) => navigate\(alarmsPath\('SUMMARY'\)\)\}/)
assert.match(alarmsPageSource, /useState\(\(\) => scopeFromParams\(searchParams\)\)/, '알람 히스토리는 쿼리로 받은 필터로 시작해야 합니다')
assert.match(alarmsPageSource, /useState\(entryScope \?\? DEFAULT_SCOPE\)/)
// 4개 KPI 타일은 같은 크기로 읽힌다 — 전체 알람만 크게 두지 않는다(멘토 피드백 2026-09-03).
assert.doesNotMatch(dashboardPageSource, /text-\[60px\]|hero:/, 'KPI 타일 수치는 같은 폰트 크기여야 합니다')
assert.match(alarmsPageSource, /ALARM_TABS = Object\.freeze\(\['ALL', 'TRACE', 'SUMMARY', 'R03'\]\)/)
assert.match(alarmsPageSource, /전체 \(\{rows\.all\.length\}\)/)
assert.match(alarmsPageSource, /\.\.\.\(tab === 'ALL' \? \['SOURCE'\] : \[\]\)/, '혼합 목록은 알람 source를 구분해야 합니다')
// 멘토 피드백(2026-09-03): 알람 표의 한계 컬럼은 탭·source와 무관하게 LSL·USL(스펙 한계)로 통일한다.
assert.match(alarmsPageSource, /const limitHeaders = \['LSL', 'USL'\]/, '알람 표 한계 컬럼은 LSL·USL이어야 합니다')
assert.doesNotMatch(alarmsPageSource, /'LCL', 'UCL'|'LOWER', 'UPPER'/, '표에 LCL·UCL·LOWER·UPPER 헤더를 남기면 안 됩니다')
assert.match(alarmsPageSource, /\{num\(lim\?\.spec_lower\)\}/, '한계 컬럼 값도 스펙 한계를 써야 합니다')
// 조치·알림 관련 컬럼은 알람 표에서 제외한다(멘토 피드백) — 조치는 Agent 화면에서 본다.
assert.doesNotMatch(alarmsPageSource, /'ACTION',|'NOTIFY'|'HIT'/, 'ACTION·NOTIFY·HIT 컬럼은 표에 없어야 합니다')
assert.doesNotMatch(alarmsPageSource, /action_code|deliveries/, '표는 조치 값을 렌더하지 않아야 합니다')
assert.doesNotMatch(alarmsPageSource, /data\.actionOf/, '표는 별도 actions 조회 없이 알람 항목만으로 조치를 표시해야 합니다')
// wafer 컬럼은 이름과 값 모두 W 표기를 쓴다.
assert.match(alarmsPageSource, /'WAFER',/, 'wafer 컬럼 헤더는 WAFER여야 합니다')
// 기간 필터는 두 화면이 같다 — 데이터 전체 기간을 기본값으로 채우고, 재조회는 걸지 않는다.
for (const [name, source] of [['대시보드', dashboardPageSource], ['알람 히스토리', alarmsPageSource]]) {
  assert.match(source, /dataDateRange\(alarms\)/, `${name} 화면은 응답 기간을 기본값으로 채워야 합니다`)
  assert.match(source, /const \{ area, equipment, chamber \} = applied/, `${name} 화면 조회는 기간 변경으로 재조회되면 안 됩니다`)
  assert.match(source, /\.\.\.DEFAULT_SCOPE, \.\.\.\(range \?\? \{\}\)/, `${name} 화면 초기화는 데이터 기간으로 돌아가야 합니다`)
}
assert.match(alarmsPageSource, /`W\$\{a\.wafer_no\}`/, 'wafer 값 앞에 W를 붙여야 합니다')
const handlerStart = alarmsPageSource.indexOf('  const handleRunAnalysis = () => {')
const handlerEnd = alarmsPageSource.indexOf('\n  const rows = useMemo', handlerStart)
const runHandler = alarmsPageSource.slice(handlerStart, handlerEnd)
assert.ok(handlerStart >= 0 && handlerEnd > handlerStart)
assert.equal((runHandler.match(/createRun\(/g) ?? []).length, 1, '분석 버튼 클릭당 POST 경로는 하나여야 합니다')
assert.match(runHandler, /analysisAction\.mode === 'OPEN'/, '기존 실행이 있으면 새 POST 전에 해당 실행으로 이동해야 합니다')
assert.match(runHandler, /navigate\(`\/agent-runs\/\$\{encodeURIComponent\(analysisAction\.runId\)\}`\)/)
assert.match(runHandler, /navigate\(`\/agent-runs\/\$\{accepted\.agent_run_id\}`\)/, '202 응답 run 상세로 이동해야 합니다')
assert.match(runHandler, /catch\(\(error\) => setRunError\(runErrorMessage\(error\)\)\)/)
assert.doesNotMatch(runHandler, /retry|setTimeout|setInterval/, '409·422·503에서 자동 재시도하면 안 됩니다')
assert.match(alarmsPageSource, /<AlarmTracePanel/, '화면 2가 공용 incident Trace 패널을 실제 렌더해야 합니다')
assert.match(alarmsPageSource, /alarmTrendScope\(selected\)/, '같은 LOT의 선택 파라미터 전체 wafer를 한 번에 조회해야 합니다')
assert.doesNotMatch(alarmsPageSource, /incidentTraceScope\(selected/, '알람이 난 wafer만으로 추세를 축소하면 안 됩니다')
assert.match(alarmsPageSource, /allowWaferSelection/, 'LOT 요약 패널에서 단일 wafer를 선택할 수 있어야 합니다')

const historyTrendSource = await readFile(new URL('../src/shared/components/trace/HistoryTrendChart.jsx', import.meta.url), 'utf8')
assert.match(historyTrendSource, /function LotWaferPanel/, 'LOT 요약·wafer 선택 패널이 있어야 합니다')
assert.match(historyTrendSource, /function waferSummary/, '선택 wafer의 상태·실측 요약을 계산해야 합니다')
assert.match(historyTrendSource, /lg:grid-cols-\[minmax\(0,1fr\)_320px\]/, '그래프와 LOT 패널을 분할해야 합니다')
assert.doesNotMatch(historyTrendSource, /챔버 추세/, '다중 wafer 추세 전환은 알람 화면에서 제거해야 합니다')
assert.match(historyTrendSource, /기준 알람 W\{alarm\.wafer_no\}/, '표에서 고른 기준 알람 wafer를 비교 선택과 구분해야 합니다')
assert.match(historyTrendSource, /현재 그래프/, '오른쪽 패널이 현재 그래프 wafer의 상태를 함께 보여줘야 합니다')
// 단일 wafer 뷰는 고정 px가 아니라 그리드 높이를 좌우가 함께 채워 하단이 정렬돼야 한다.
assert.match(historyTrendSource, /lg:h-\[500px\] lg:grid-cols-\[minmax\(0,1fr\)_320px\]/, '그래프와 오른쪽 패널이 같은 높이 상자를 공유해야 합니다')
assert.match(historyTrendSource, /height="100%"/, '단일 wafer 그래프는 컨테이너 높이를 채워야 합니다')
assert.match(historyTrendSource, /overflow-y-auto/, '오른쪽 패널은 넘칠 때 내부 스크롤해야 합니다')
assert.doesNotMatch(historyTrendSource, /viewMode === 'selected' \? 500/, '고정 500px 높이로 되돌리면 하단 정렬이 깨집니다')

// 차트의 색 예산은 이상 신호(OOS·OOC)에만 쓴다.
// 계열은 hue를 바꾸지 않고 navy→blue 명도 ramp로 구분하고, 한계선은 중립 회색으로 물러난다.
const traceChartSource = await readFile(new URL('../src/shared/components/trace/TraceChart.jsx', import.meta.url), 'utf8')
const seriesPalette = traceChartSource.match(/const COLORS = \[([^\]]*)\]/)?.[1] ?? ''
for (const banned of ['#dc2626', '#d97706', '#f59e0b', '#16a34a', '#15803d', '#7c3aed', '#db2777', '#0891b2']) {
  assert.ok(!seriesPalette.includes(banned), `WAFER 계열색은 단일 hue ramp여야 합니다 (${banned} 발견)`)
}
// 멘토 피드백(2026-09-03) #3: OOS·OOC를 면으로 칠하지 않고 USL·UCL·TGT·LCL·LSL을
// 색이 있는 가로 점선으로만 표시한다. spec(적) · control(황) · target(청)으로 층을 나누고
// 상·하한은 같은 색에서 dash 길이로 구분한다.
const limitBlock = traceChartSource.match(/const LIMIT_STYLE = \{([\s\S]*?)\}\n/)?.[1] ?? ''
for (const label of ['USL', 'LSL', 'UCL', 'LCL', 'TARGET']) {
  assert.match(limitBlock, new RegExp(`${label}: \\{ color: '#`), `${label} 한계선에 색을 지정해야 합니다`)
}
assert.match(traceChartSource, /USL: \{ color: '#c2384a', dash: '9 5'/)
assert.match(traceChartSource, /LSL: \{ color: '#c2384a', dash: '3 5'/)
assert.match(traceChartSource, /UCL: \{ color: '#c07a12', dash: '9 5'/)
assert.match(traceChartSource, /LCL: \{ color: '#c07a12', dash: '3 5'/)
assert.match(traceChartSource, /TARGET: \{ color: '#2f5fa8'/)
assert.match(traceChartSource, /fill=\{line \? limitColor\(line\.styleLabel\) : '#64748b'\}/, 'Y축 한계 라벨은 해당 점선과 같은 색이어야 합니다')
// 축 밖으로 밀린 ReferenceLine은 recharts가 버린다 — 다섯 선이 모두 보이려면 도메인에 포함해야 한다.
assert.match(traceChartSource, /traceYAxisDomain\(wafers, limit, \{ includeAllLimits: true \}\)/, 'LCL·LSL이 실측 범위 밖이어도 축에 남아야 합니다')
// recharts YAxis 기본 interval='preserveEnd'는 라벨이 겹치면 조용히 버린다 —
// 측정값이 넓게 퍼진 알람에서 UCL·LCL 라벨이 사라지던 원인. 전부 그리게 하고
// 겹칠 만큼 가까운 한계는 우리가 먼저 하나만 남긴다.
assert.match(traceChartSource, /interval=\{0\}/, '축 눈금 라벨을 recharts가 임의로 버리게 두면 안 됩니다')
assert.match(traceChartSource, /function axisLimitLines/, '겹치는 한계 라벨은 축에서 하나만 남겨야 합니다')
assert.match(traceChartSource, /tick=\{<YAxisTick lines=\{axisLimits\} \/>\} width=\{78\}/, '축 라벨은 정리한 한계 목록을 써야 합니다')
assert.doesNotMatch(traceChartSource, /LimitLegend/, '한계 값은 Y축에서 읽는다 — 별도 범례를 다시 넣지 않는다')
// 이상 판정은 한계선·구간으로 읽고 실측점 자체는 동일한 작은 원형 marker를 쓴다.
const pointDotSource = traceChartSource.slice(
  traceChartSource.indexOf('function PointDot'),
  traceChartSource.indexOf('export default function TraceChart'),
)
const traceTooltipSource = traceChartSource.slice(
  traceChartSource.indexOf('function TraceTooltip'),
  traceChartSource.indexOf('function PointDot'),
)
assert.match(traceChartSource, /const SINGLE_COLOR = '#47769d'/)
assert.match(traceChartSource, /strokeWidth=\{selectedView \? 2\.8 : 2\}/, '단일 실측선은 한계선보다 굵어야 합니다')
assert.match(traceChartSource, /function YAxisTick/, '한계 라벨은 일반 데이터 눈금과 같은 Y축에 통합해야 합니다')
assert.match(traceChartSource, /fontSize=\{line \? 11 : 10\}/, '한계 라벨은 일반 눈금보다 크게 표시해야 합니다')
assert.match(traceChartSource, /fontWeight=\{line \? 700 : 400\}/, '한계 라벨은 일반 눈금보다 굵게 표시해야 합니다')
assert.match(traceChartSource, /Number\(payload\.value\.toFixed\(1\)\)\.toString\(\)/, '일반 Y축 눈금은 불필요한 세 자리 소수를 표시하지 않아야 합니다')
assert.match(traceChartSource, /Math\.abs\(tick - line\.value\) < span \* 0\.055/, '한계와 가까운 일반 눈금은 중복 표시하지 않아야 합니다')
assert.match(traceChartSource, /width=\{78\} \/>/, 'Y축 폭은 한계 라벨과 데이터 눈금이 한 줄에서 읽힐 정도만 확보해야 합니다')
assert.match(traceChartSource, /function visibleLimitLines/, '같은 값의 한계 라벨은 겹치지 않게 합쳐야 합니다')
assert.match(traceChartSource, /label: `\$\{existing\.label\}\/\$\{line\.label\}`/, 'LCL과 LSL 값이 같으면 LCL\/LSL로 표시해야 합니다')
// 멘토 피드백 #4: 측정 시각 그래프의 실측점은 흰 점이 아니라 빨간 점이다.
assert.match(traceChartSource, /const POINT_COLOR = '#e03131'/, '실측점 색을 상수로 고정해야 합니다')
assert.match(traceChartSource, /fill=\{selectedView \? POINT_COLOR : '#fff'\}/, '단일 wafer(측정 시각) 그래프의 실측점은 빨간 점이어야 합니다')
assert.match(traceChartSource, /r=\{3\.2\} fill=\{fill\}/, '실측점은 판정과 무관하게 동일한 작은 원으로 표시해야 합니다')
assert.doesNotMatch(traceChartSource, /const ALERT_COLOR/, '실측점은 OOS·OOC 전용 색을 사용하지 않아야 합니다')
assert.doesNotMatch(pointDotSource, /judgeValue|OOS|OOC/, '실측점 renderer는 판정에 따라 모양·색을 바꾸지 않아야 합니다')
assert.match(traceTooltipSource, /min-w-\[260px\] max-w-\[400px\]/, 'hover tooltip은 발표 화면에서 읽을 수 있는 폭이어야 합니다')
assert.match(traceTooltipSource, /text-\[12\.5px\]/, 'hover tooltip 제목은 작은 보조 글씨보다 크게 표시해야 합니다')
assert.doesNotMatch(traceTooltipSource, /\{entry\.value\}|\{judgement\}/, 'hover tooltip은 실측값과 OOS·OOC 판정명을 중복 표시하지 않아야 합니다')
assert.match(traceTooltipSource, /limitDifference\(entry\.value, limit, judgeValue\(entry\.value, limit\)\)/, 'hover tooltip은 한계선 대비 차이를 유지해야 합니다')
assert.match(traceTooltipSource, /\{difference\}/, '계산한 한계선 대비 차이를 사용자에게 표시해야 합니다')
assert.match(traceTooltipSource, /formatMeasuredAt\(point\?\.measured_at\)/, 'hover tooltip은 원본 timestamp를 직접 노출하지 않아야 합니다')
assert.match(traceTooltipSource, />\{measuredTime\}<\/div>/, 'hover tooltip은 정리한 한국 기준 연월일·시각만 표시해야 합니다')
assert.doesNotMatch(traceTooltipSource, /측정 시각|\{point\?\.measured_at\}/, 'hover tooltip은 시각 라벨이나 ISO timestamp를 그대로 표시하지 않아야 합니다')
assert.doesNotMatch(traceChartSource, /<path d=\{`M \$\{cx\}/, '상태 marker를 다이아몬드·삼각형으로 표시하지 않아야 합니다')
assert.doesNotMatch(traceChartSource, /ReferenceArea/, 'OOS·OOC 영역을 면으로 칠하면 안 됩니다 (멘토 피드백 #3)')
assert.doesNotMatch(traceChartSource, /function limitAreas/, '한계 영역 계산은 점선 표시로 대체돼야 합니다')
assert.doesNotMatch(traceChartSource, /OOS 영역|OOC 영역/, '그래프에 이탈 영역 면·라벨을 남기면 안 됩니다')
assert.doesNotMatch(historyTrendSource, /text-\[#|bg-\[#/, '상태 칩은 임의 hex 대신 토큰 클래스를 써야 합니다')
assert.match(historyTrendSource, /OOS: \{ label: 'OOS', text: 'text-trace-oos', dot: 'bg-trace-oos' \}/, '오른쪽 패널 OOS는 그래프 OOS 한계 영역과 같은 의미색 토큰을 써야 합니다')
assert.match(historyTrendSource, /OOC: \{ label: 'OOC', text: 'text-trace-ooc', dot: 'bg-trace-ooc' \}/, '오른쪽 패널 OOC는 그래프 OOC 한계 영역과 같은 의미색 토큰을 써야 합니다')
// Tailwind 기본 팔레트(slate-*) 대신 프로젝트 토큰만 쓴다.
assert.doesNotMatch(historyTrendSource, /(?:bg|text|border)-slate-\d/, '패널은 프로젝트 색 토큰만 써야 합니다')
// 웨이퍼 격자에서도 채도는 이상 신호에만 — 정상은 점이 없어야 한다.
assert.match(historyTrendSource, /OK: \{ label: '정상', text: 'text-navy', dot: '' \}/)
assert.match(historyTrendSource, /UNKNOWN: \{ label: '미확인', text: 'text-g2', dot: 'border border-dash-line' \}/)
assert.doesNotMatch(historyTrendSource, />!<\/span>/, '기준 알람을 빨간 느낌표로 표시하지 않아야 합니다')
assert.match(historyTrendSource, /WAFER_STATUS\[status\]\.dot/, 'wafer 상태는 작은 점으로만 구분해야 합니다')
assert.match(historyTrendSource, /function boundaryAlert/, '선택 wafer의 알람 발생 경계를 문장으로 설명해야 합니다')
assert.match(historyTrendSource, /USL \$\{limit\.spec_upper\} 초과 · OOS \$\{upperOos\} point/, '상한 초과 원인과 point 수를 함께 표시해야 합니다')
assert.match(historyTrendSource, /UCL \$\{limit\.ctrl_upper\} 초과 · OOC \$\{upperOoc\} point/, '상한 OOC 원인과 point 수를 함께 표시해야 합니다')
assert.match(historyTrendSource, /LCL \$\{limit\.ctrl_lower\} 미만 · OOC \$\{lowerOoc\} point/, '하한 OOC 원인과 point 수를 함께 표시해야 합니다')
assert.doesNotMatch(historyTrendSource, /bg-page/, '오른쪽 패널은 정의되지 않은 배경 유틸리티를 쓰면 안 됩니다')
assert.match(historyTrendSource, /border border-line bg-soft/, '오른쪽 패널 기본면은 회사 포털의 옅은 청회색이어야 합니다')
assert.match(historyTrendSource, /border border-tint-blue-line bg-tint-blue px-3 py-2\.5/, '현재 그래프 영역만 회사 청색 tint로 강조해야 합니다')

const layoutSource = await readFile(new URL('../src/app/Layout.jsx', import.meta.url), 'utf8')
assert.match(layoutSource, /border-r border-line bg-white/, '사이드바는 회사 포털과 같은 라이트 서피스여야 합니다')
assert.match(layoutSource, /bg-tint-blue font-bold text-blue/, '활성 메뉴만 회사 청색 tint로 강조해야 합니다')
assert.doesNotMatch(layoutSource, /bg-navy text-white/, '진한 네이비 사이드바로 되돌리면 안 됩니다')

// ── 화면 간 필터 전달(대시보드 KPI → 알람 히스토리) ─────────────────
const carried = scopeToParams(
  { from: '2026-06-02', to: '2026-06-03', area: 'AREA1', equipment: 'EQP04', chamber: ALL },
  { tab: 'TRACE' },
)
assert.equal(
  carried.toString(),
  new URLSearchParams({ tab: 'TRACE', from: '2026-06-02', to: '2026-06-03', area: 'AREA1', equipment: 'EQP04' }).toString(),
  'KPI 이동 쿼리는 tab과 적용 필터를 함께 실어야 합니다',
)
assert.equal(carried.get('chamber'), null, '전체(미지정) 필터는 쿼리에 싣지 않아야 합니다')
assert.deepEqual(
  scopeFromParams(carried),
  { from: '2026-06-02', to: '2026-06-03', area: 'AREA1', equipment: 'EQP04', chamber: ALL },
  '알람 히스토리는 넘겨받은 필터를 그대로 복원해야 합니다',
)
assert.equal(scopeFromParams(new URLSearchParams({ tab: 'ALL' })), null, '필터 없는 진입은 화면 기본 동작을 유지해야 합니다')
assert.deepEqual(
  scopeFromParams(new URLSearchParams({ area: 'AREA2' })),
  { ...DEFAULT_SCOPE, area: 'AREA2' },
  '쿼리에 실린 키만 덮어쓰고 나머지는 기본값이어야 합니다',
)
// 쿼리로 진입한 필터를 바꾼 뒤 행을 눌러도 tab·source와 함께 필터가 유지된다.
const afterSelect = scopeToParams({ ...DEFAULT_SCOPE, from: '2026-06-04' }, carried)
afterSelect.set('source', 'TRACE')
assert.equal(afterSelect.get('from'), '2026-06-04')
assert.equal(afterSelect.get('area'), null, '해제된 필터는 URL에서 지워져야 합니다')
assert.equal(afterSelect.get('tab'), 'TRACE', '탭 선택은 필터 갱신에도 유지되어야 합니다')

console.log('detection-screen-state: 기간 필터·화면 간 필터 전달 계약 통과')

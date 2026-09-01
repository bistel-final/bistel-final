import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import {
  analysisActionOf,
  hasDashboardResults,
  partitionAlarms,
  periodLabel,
  runErrorMessage,
} from '../src/features/detection/detection-screen-state.js'
import { getAlarm, searchTraces } from '../src/shared/api/detection.js'
import { alarmTrendScope } from '../src/shared/trace/incidentTrace.js'
import { formatMeasuredAt, selectedWaferChartModel } from '../src/shared/trace/traceModel.js'

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
assert.match(dashboardPageSource, /onTotal=\{\(\) => navigate\('\/alarms\?tab=ALL'\)\}/, '대시보드 전체 알람은 전체 히스토리로 이동해야 합니다')
assert.match(alarmsPageSource, /ALARM_TABS = Object\.freeze\(\['ALL', 'TRACE', 'SUMMARY', 'R03'\]\)/)
assert.match(alarmsPageSource, /전체 \(\{rows\.all\.length\}\)/)
assert.match(alarmsPageSource, /\.\.\.\(tab === 'ALL' \? \['SOURCE'\] : \[\]\)/, '혼합 목록은 알람 source를 구분해야 합니다')
assert.match(alarmsPageSource, /a\.source === 'SUMMARY' \? lim\?\.ctrl_lower : lim\?\.spec_lower/, '전체 목록의 하한은 source별 계약을 사용해야 합니다')
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
// 한계선은 채도 있는 의미색을 쓰지 않는다 — 항상 떠 있어 색 예산을 소진한다.
const limitBlock = traceChartSource.match(/const LIMIT_STYLE = \{([\s\S]*?)\}\n/)?.[1] ?? ''
for (const banned of ['#dc2626', '#d97706', '#f59e0b', '#2563eb']) {
  assert.ok(!limitBlock.includes(banned), `한계선은 중립 회색이어야 합니다 (${banned} 발견)`)
}
// 상·하한은 색이 아니라 dash 길이로 구분한다.
assert.match(traceChartSource, /USL: \{ color: '#5f6d7c', dash: '8 5', opacity: 0\.96, width: 1\.55 \}/)
assert.match(traceChartSource, /LSL: \{ color: '#5f6d7c', dash: '3 5', opacity: 0\.96, width: 1\.55 \}/)
assert.match(traceChartSource, /UCL: \{ color: '#9ca8b5', dash: '8 5', opacity: 0\.84, width: 1\.15 \}/)
assert.match(traceChartSource, /LCL: \{ color: '#9ca8b5', dash: '3 5', opacity: 0\.84, width: 1\.15 \}/)
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
assert.match(traceChartSource, /tick=\{<YAxisTick lines=\{displayedLimits\} \/>\} width=\{78\}/, 'Y축 폭은 한계 라벨과 데이터 눈금이 한 줄에서 읽힐 정도만 확보해야 합니다')
assert.match(traceChartSource, /function visibleLimitLines/, '같은 값의 한계 라벨은 겹치지 않게 합쳐야 합니다')
assert.match(traceChartSource, /label: `\$\{existing\.label\}\/\$\{line\.label\}`/, 'LCL과 LSL 값이 같으면 LCL\/LSL로 표시해야 합니다')
assert.match(traceChartSource, /<circle cx=\{cx\} cy=\{cy\} r=\{3\} fill="#fff" stroke=\{stroke \?\? SINGLE_COLOR\} strokeWidth=\{1\.5\}/, '실측점은 판정과 무관하게 동일한 작은 원으로 표시해야 합니다')
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
assert.match(traceChartSource, /ReferenceArea/, '선택 wafer 그래프는 실제 OOS 구간을 면으로 표시해야 합니다')
assert.match(traceChartSource, /OOS 영역 · USL 초과/, '상한 이탈 영역의 의미를 직접 표시해야 합니다')
assert.match(traceChartSource, /OOS 영역 · LSL 미만/, '하한 이탈 영역의 의미를 직접 표시해야 합니다')
assert.match(traceChartSource, /OOC 영역 · UCL~USL/, '상한 관리 이탈 영역을 OOS와 다른 면으로 표시해야 합니다')
assert.match(traceChartSource, /OOC 영역 · LSL~LCL/, '하한 관리 이탈 영역을 OOS와 다른 면으로 표시해야 합니다')
assert.match(traceChartSource, /function limitAreas/, 'OOS·OOC 영역을 현재 Y축 범위에 맞게 계산해야 합니다')
assert.match(traceChartSource, /const showLabel = y2 - y1 >= \(domainMax - domainMin\) \* 0\.08/, '얇은 영역의 문구는 X축과 겹치지 않게 숨겨야 합니다')
assert.doesNotMatch(historyTrendSource, /text-\[#|bg-\[#/, '상태 칩은 임의 hex 대신 토큰 클래스를 써야 합니다')
assert.match(historyTrendSource, /OOS: \{ label: 'OOS', text: 'text-trace-oos', dot: 'bg-trace-oos' \}/, '오른쪽 패널 OOS는 그래프 OOS 한계 영역과 같은 의미색 토큰을 써야 합니다')
assert.match(historyTrendSource, /OOC: \{ label: 'OOC', text: 'text-trace-ooc', dot: 'bg-trace-ooc' \}/, '오른쪽 패널 OOC는 그래프 OOC 한계 영역과 같은 의미색 토큰을 써야 합니다')
assert.match(traceChartSource, /var\(--color-trace-oos\)/, '그래프 OOS 한계 영역은 공용 trace OOS 토큰을 써야 합니다')
assert.match(traceChartSource, /var\(--color-trace-ooc\)/, '그래프 OOC 한계 영역은 공용 trace OOC 토큰을 써야 합니다')
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

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getAllAlarms, getDashboard } from '../../../shared/api/detection.js'
import { getActions } from '../../../shared/api/agent.js'
import LoadingState, { Skeleton } from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { Card } from '../../../shared/components/ui/Card.jsx'
import ScopeFilterBar from '../components/ScopeFilterBar.jsx'
import { ALL, DEFAULT_SCOPE, scopeToParams } from '../components/scopeModel.js'
import { dataDateRange, hasDashboardResults } from '../detection-screen-state.js'
import {
  ChartCard,
  LegendRow,
  OOC_HEX,
  OOC_TEXT_HEX,
  OOS_HEX,
  OOS_TEXT_HEX,
  SKY_HEX,
  StackBars,
  TrendLine,
} from '../components/DashCharts.jsx'

// 알람 대시보드 — 라이트 시안 1번. #260 발표 스케일 재설계:
//   히어로 밴드(전체·OOS/OOC 비율·조치) → 전폭 추이 → 전폭 분포(탭)
// 정보 구조·집계 로직(V5-A-3.3 실 API 연동)은 그대로, 배치·크기·색만 재편했다.
// 기간·유형 분리 집계는 summary 응답에 없다 — 알람을 넓게 받아 클라이언트에서 집계한다.
// TODO(api): /dashboard/summary 에 기간·judgement 분리 집계 파라미터가 정의되면 서버 집계로 환원.
const WIDE = 100

const dayLabel = (d) => `${Number(d.slice(5, 7))}/${Number(d.slice(8, 10))}`
const dateOf = (iso) => String(iso ?? '').slice(0, 10)
// runs/actions 목록에는 area 파라미터가 없다 — 설비 ID 접두어는 실제 데이터(EQP01..)와
// 무관한 시안 전용 규칙이라 쓸 수 없다. 대신 실 hierarchy(GET /dashboard/summary)의
// area_id·equipment_id 매핑에서 유도한다(buildAreaOfEquipment).
const buildAreaOfEquipment = (hierarchy) =>
  Object.fromEntries((hierarchy ?? []).map((h) => [h.equipment_id, h.area_id]))
const inRange = (iso, { from, to }) => {
  const d = dateOf(iso)
  return (!from || d >= from) && (!to || d <= to)
}

// {키: {oos, ooc}} 집계 → StackBars 데이터
const stackOf = (rows, keyOf) => {
  const map = {}
  for (const a of rows) {
    const k = keyOf(a)
    map[k] ??= { label: k, oos: 0, ooc: 0 }
    if (a.judgement === 'OOS') map[k].oos += 1
    else map[k].ooc += 1
  }
  return Object.values(map).sort((x, y) => y.oos + y.ooc - (x.oos + x.ooc) || x.label.localeCompare(y.label))
}

// ── 히어로 밴드 — 이 화면에서 가장 먼저 읽혀야 하는 세 가지 ───────────
function HeroBand({ agg, onTotal, onOos, onOoc, onAction }) {
  // 4칸 같은 문법: 전체 | OOS | OOC | 조치 완료 — 누르면 그 목록으로 (호버에 힌트가 파랑으로)
  const tiles = [
    { label: '전체 알람', value: agg.total, color: null, hint: '알람 히스토리 보기 →', onClick: onTotal },
    { label: 'OOS', value: agg.oos, color: OOS_HEX, valueColor: OOS_TEXT_HEX, hint: 'TRACE 알람 보기 →', onClick: onOos },
    { label: 'OOC', value: agg.ooc, color: OOC_HEX, valueColor: OOC_TEXT_HEX, hint: 'SUMMARY 알람 보기 →', onClick: onOoc },
    { label: '조치 완료', value: agg.mesSent, color: SKY_HEX, hint: 'Agent 분석 · 승인 보기 →', onClick: onAction },
  ]
  return (
    <Card className="grid grid-cols-[1.15fr_1fr_1fr_1fr] divide-x divide-line">
      {tiles.map((t) => (
        <button
          key={t.label}
          type="button"
          onClick={t.onClick}
          className="group cursor-pointer px-7 py-6 text-left transition-colors first:rounded-l-[10px] last:rounded-r-[10px] hover:bg-soft focus:outline-none focus-visible:ring-2 focus-visible:ring-tint-blue-line"
        >
          <div className="flex items-center gap-2 text-[12.5px] font-bold tracking-[.06em] text-g2">
            {t.color && <span className="h-2.5 w-2.5 rounded-full" style={{ background: t.color }} />}
            {t.label}
          </div>
          <div className="mt-1 font-mono text-[44px] font-extrabold leading-none tracking-[-.02em] text-navy" style={t.valueColor ? { color: t.valueColor } : undefined}>
            {t.value}
          </div>
          <div className="mt-2.5 text-[12px] text-faint transition-colors group-hover:text-blue">{t.hint}</div>
        </button>
      ))}
    </Card>
  )
}

// ── 분포 카드 — 챔버 | 설비 | 파라미터 탭 (같은 그림 3장을 1장으로) ──
// 추이 차트와 같은 전폭·같은 높이로 둔다(멘토 피드백 #2).
const DIST_TABS = [
  { key: 'chamber', label: '챔버' },
  { key: 'equipment', label: '설비' },
  { key: 'sensor', label: '파라미터' },
]
function DistributionCard({ agg }) {
  const [tab, setTab] = useState('chamber')
  const data = tab === 'chamber' ? agg.byChamber : tab === 'equipment' ? agg.byEquipment : agg.bySensor
  return (
    <ChartCard
      title="알람 분포"
      action={
        <div className="flex gap-1.5">
          {DIST_TABS.map((t) => (
            <Button key={t.key} sm variant={tab === t.key ? 'primary' : 'outline'} onClick={() => setTab(t.key)}>
              {t.label}
            </Button>
          ))}
        </div>
      }
    >
      <StackBars data={data} height={300} />
      <LegendRow
        items={[
          { label: 'OOS', color: OOS_HEX },
          { label: 'OOC', color: OOC_HEX },
        ]}
      />
    </ChartCard>
  )
}

function DashboardPage() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [draft, setDraft] = useState(DEFAULT_SCOPE)
  const [applied, setApplied] = useState(DEFAULT_SCOPE)
  // 응답에서 유도한 데이터 기간 — 기간 필터의 기본값·초기화 기준
  const [range, setRange] = useState(null)
  const rangeSet = useRef(false)

  // 조회 파라미터는 AREA·설비·챔버뿐이다(기간은 클라이언트 필터) —
  // 기간 기본값을 채워 넣을 때 불필요한 재조회가 걸리지 않도록 그 셋만 의존한다.
  const { area, equipment, chamber } = applied
  const load = useCallback(() => {
    const scope = {}
    if (area !== ALL) scope.area = area
    if (equipment !== ALL) scope.equipment_id = equipment
    if (chamber !== ALL) scope.chamber_id = chamber
    Promise.all([
      getDashboard({}), // 필터 선택지(hierarchy)는 항상 전체 계층에서 만든다
      getAllAlarms(scope), // size 상한(100)보다 총 건수가 많을 수 있어 전체 페이지를 순회한다
      getActions({ ...(scope.equipment_id ? { equipment_id: scope.equipment_id } : null), ...(scope.chamber_id ? { chamber_id: scope.chamber_id } : null), page: 1, size: WIDE }),
    ])
      .then(([summary, alarmPage, actionPage]) => {
        const alarms = alarmPage.items ?? []
        setData({ summary, alarms, actions: actionPage.items ?? [] })
        // 기간 기본값 — 첫 응답이 덮는 일자 범위를 그대로 필터에 채운다(멘토 피드백 #1).
        if (rangeSet.current) return
        const derived = dataDateRange(alarms)
        if (!derived) return
        rangeSet.current = true
        setRange(derived)
        setDraft((prev) => ({ ...prev, ...derived }))
        setApplied((prev) => ({ ...prev, ...derived }))
      })
      .catch((e) => setError(e.message))
  }, [area, equipment, chamber])
  useEffect(() => {
    load()
  }, [load])

  // 기간은 클라이언트에서 거른다 — 위 TODO(api) 참조
  const agg = useMemo(() => {
    if (!data) return null
    const { alarms, actions } = data
    const period = { from: applied.from, to: applied.to }
    const areaOfEqp = buildAreaOfEquipment(data.summary.hierarchy)
    const areaOk = (id) => applied.area === ALL || areaOfEqp[id] === applied.area
    const rows = alarms.filter((a) => inRange(a.occurred_at, period))
    const actRows = actions.filter((a) => inRange(a.created_at, period) && areaOk(a.equipment_id))

    const oos = rows.filter((a) => a.judgement === 'OOS').length
    const ooc = rows.length - oos
    const delivered = (action, channel, status) =>
      (action.deliveries ?? []).some((delivery) => delivery.channel === channel && delivery.status === status)
    const mesSent = actRows.filter((action) => delivered(action, 'MES', 'SENT')).length

    const byDay = {}
    for (const a of rows) {
      const d = dateOf(a.occurred_at)
      byDay[d] ??= { label: dayLabel(d), date: d, oos: 0, ooc: 0 } // date: hover 툴팁 표기용 원본 일자
      if (a.judgement === 'OOS') byDay[d].oos += 1
      else byDay[d].ooc += 1
    }
    const daily = Object.keys(byDay)
      .sort()
      .map((d) => byDay[d])

    return {
      total: rows.length,
      oos,
      ooc,
      mesSent,
      daily,
      byChamber: stackOf(rows, (a) => a.chamber_id),
      byEquipment: stackOf(rows, (a) => a.equipment_id),
      bySensor: stackOf(rows, (a) => a.sensor_id),
    }
  }, [data, applied])

  if (error)
    return (
      <ErrorState
        detail={error}
        onRetry={() => {
          setError(null)
          load()
        }}
      />
    )
  if (!data || !agg)
    return (
      <LoadingState message="대시보드 데이터를 불러오는 중…">
        <Skeleton className="h-[150px]" />
        <Skeleton className="h-[360px]" />
      </LoadingState>
    )

  const hierarchy = data.summary.hierarchy ?? []
  const resetScope = { ...DEFAULT_SCOPE, ...(range ?? {}) }
  // KPI 클릭 이동 — 지금 적용된 필터(기간·AREA·설비·챔버)를 쿼리로 넘겨
  // 알람 히스토리가 대시보드와 같은 집계 범위로 열리게 한다.
  const alarmsPath = (tab) => `/alarms?${scopeToParams(applied, { tab })}`

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-16 items-center pb-1.5 pt-3.5">
        <div className="text-[22px] font-extrabold tracking-[-.01em] text-ink">알람 대시보드</div>
      </div>

      <ScopeFilterBar
        hierarchy={hierarchy}
        draft={draft}
        onDraft={setDraft}
        onApply={() => setApplied(draft)}
        onReset={() => {
          setDraft(resetScope)
          setApplied(resetScope)
        }}
      />

      {!hasDashboardResults(agg) ? (
        <div className="mt-4 rounded-xl border border-line bg-white py-12">
          <EmptyState title="조건에 맞는 알람이 없습니다" description="기간·AREA·설비·챔버 필터를 조정해 주세요." />
        </div>
      ) : (
        <div className="mt-2 flex flex-col gap-5">
          <HeroBand
            agg={agg}
            onTotal={() => navigate(alarmsPath('ALL'))}
            onOos={() => navigate(alarmsPath('TRACE'))}
            onOoc={() => navigate(alarmsPath('SUMMARY'))}
            onAction={() => navigate('/agent-runs')}
          />

          <ChartCard title="일자별 알람 추이" note="OOS · OOC 일별 건수">
            <TrendLine data={agg.daily} height={300} />
            <LegendRow
              items={[
                { label: 'OOS', color: OOS_HEX },
                { label: 'OOC', color: OOC_HEX },
              ]}
            />
          </ChartCard>

          <DistributionCard agg={agg} />
        </div>
      )}
    </div>
  )
}

export default DashboardPage

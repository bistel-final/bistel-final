import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getAlarms, getDashboard } from '../../../shared/api/detection.js'
import { getActions, getRuns } from '../../../shared/api/agent.js'
import LoadingState, { Skeleton } from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import ScopeFilterBar from '../components/ScopeFilterBar.jsx'
import { ALL, DEFAULT_SCOPE } from '../components/scopeModel.js'
import {
  BLUE_HEX,
  ChartCard,
  Donut,
  GRAY_HEX,
  GREEN_HEX,
  LegendRow,
  OOC_HEX,
  OOS_HEX,
  StackBars,
  TrendLine,
  ValueBars,
} from '../components/DashCharts.jsx'

// 알람 대시보드 — 라이트 시안 1번. KPI 4장(전부 클릭 이동형) + 차트 8장(2열 grid).
// 기간·유형 분리 집계는 summary 응답에 없다 — 알람을 넓게 받아 클라이언트에서 집계한다.
// TODO(api): /dashboard/summary 에 기간·judgement 분리 집계 파라미터가 정의되면 서버 집계로 환원.
const WIDE = 200

// Fault 분류 도넛 색 — 시안 뱃지 색 대응 (RFM·CDX red / MFD amber / TMD sky / FOC violet / OTH gray)
const FAULT_HEX = { RFM: OOS_HEX, CDX: OOS_HEX, MFD: OOC_HEX, TMD: '#0ea5e9', FOC: '#7c3aed', OTH: GRAY_HEX }
// 조치 도넛 색 — MONITOR gray / LOT_HOLD amber / EQP_HOLD red
const ACTION_HEX = { MONITOR: GRAY_HEX, LOT_HOLD: OOC_HEX, EQP_HOLD: OOS_HEX }

const dayLabel = (d) => `${Number(d.slice(5, 7))}/${Number(d.slice(8, 10))}`
const dateOf = (iso) => String(iso ?? '').slice(0, 10)
// runs/actions 목록에는 area 파라미터가 없다 — 설비 prefix 로 클라이언트에서 보정한다
const AREA_BY_PREFIX = { PHO: 'PHOTO', ETC: 'ETCH' }
const areaOfEqp = (id) => AREA_BY_PREFIX[String(id ?? '').slice(0, 3)] ?? null
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

function KpiCard({ label, value, color, hint, onClick }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="cursor-pointer rounded-xl border border-line bg-white px-5 py-4 text-left transition-[border-color,box-shadow,transform] duration-150 hover:-translate-y-0.5 hover:border-tint-blue-line hover:shadow-[0_8px_20px_rgba(37,99,235,.14)]"
    >
      <div className="text-[11px] font-bold tracking-[.04em] text-g2">{label}</div>
      <div className="mt-1 font-mono text-[26px] font-extrabold leading-tight" style={{ color }}>
        {value}
      </div>
      <div className="mt-1.5 text-[11px] text-faint">{hint}</div>
    </button>
  )
}

function DashboardPage() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [draft, setDraft] = useState(DEFAULT_SCOPE)
  const [applied, setApplied] = useState(DEFAULT_SCOPE)

  const load = useCallback(() => {
    const scope = {}
    if (applied.area !== ALL) scope.area = applied.area
    if (applied.equipment !== ALL) scope.equipment_id = applied.equipment
    if (applied.chamber !== ALL) scope.chamber_id = applied.chamber
    Promise.all([
      getDashboard({}), // 필터 선택지(hierarchy)는 항상 전체 계층에서 만든다
      getAlarms({ ...scope, page: 1, size: WIDE }),
      getRuns({ ...(scope.equipment_id ? { equipment_id: scope.equipment_id } : null), ...(scope.chamber_id ? { chamber_id: scope.chamber_id } : null), page: 1, size: WIDE }),
      getActions({ ...(scope.equipment_id ? { equipment_id: scope.equipment_id } : null), ...(scope.chamber_id ? { chamber_id: scope.chamber_id } : null), page: 1, size: WIDE }),
    ])
      .then(([summary, alarmPage, runPage, actionPage]) =>
        setData({ summary, alarms: alarmPage.items ?? [], runs: runPage.items ?? [], actions: actionPage.items ?? [] }),
      )
      .catch((e) => setError(e.message))
  }, [applied])
  useEffect(() => {
    load()
  }, [load])

  // 기간은 클라이언트에서 거른다 — 위 TODO(api) 참조
  const agg = useMemo(() => {
    if (!data) return null
    const { alarms, runs, actions } = data
    const range = { from: applied.from, to: applied.to }
    const areaOk = (id) => applied.area === ALL || areaOfEqp(id) === applied.area
    const rows = alarms.filter((a) => inRange(a.occurred_at, range))
    const runRows = runs.filter((r) => inRange(r.started_at, range) && areaOk(r.equipment_id))
    const actRows = actions.filter((a) => inRange(a.created_at, range) && areaOk(a.equipment_id))

    const oos = rows.filter((a) => a.judgement === 'OOS').length
    const ooc = rows.length - oos
    const mesSent = actRows.filter((a) => a.send_channel === 'MES' && a.send_status === 'SENT').length

    const byDay = {}
    for (const a of rows) {
      const d = dateOf(a.occurred_at)
      byDay[d] ??= { label: dayLabel(d), oos: 0, ooc: 0 }
      if (a.judgement === 'OOS') byDay[d].oos += 1
      else byDay[d].ooc += 1
    }
    const daily = Object.keys(byDay)
      .sort()
      .map((d) => byDay[d])

    const faultMap = {}
    for (const r of runRows) faultMap[r.fault_code] = (faultMap[r.fault_code] ?? 0) + 1
    const faults = Object.entries(faultMap)
      .sort((x, y) => y[1] - x[1])
      .map(([code, value]) => ({ label: code, value, color: FAULT_HEX[code] ?? GRAY_HEX }))

    const actionMap = {}
    for (const a of actRows) actionMap[a.action_code] = (actionMap[a.action_code] ?? 0) + 1
    const actionSlices = ['EQP_HOLD', 'LOT_HOLD', 'MONITOR']
      .filter((c) => actionMap[c])
      .map((c) => ({ label: c, value: actionMap[c], color: ACTION_HEX[c] }))

    const notify = [
      { label: 'Email', value: actRows.filter((a) => a.send_channel === 'EMAIL' && a.send_status === 'SENT').length, color: GREEN_HEX },
      { label: 'MES', value: mesSent, color: BLUE_HEX },
      { label: 'None', value: actRows.filter((a) => a.send_status !== 'SENT').length, color: GRAY_HEX },
    ]

    return {
      total: rows.length,
      oos,
      ooc,
      mesSent,
      daily,
      byChamber: stackOf(rows, (a) => a.chamber_id),
      byEquipment: stackOf(rows, (a) => a.equipment_id),
      bySensor: stackOf(rows, (a) => a.sensor_id),
      ratio: [
        { label: 'OOS', value: oos, color: OOS_HEX },
        { label: 'OOC', value: ooc, color: OOC_HEX },
      ],
      faults,
      actionSlices,
      notify,
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
        <Skeleton className="h-[110px]" />
        <Skeleton className="h-[360px]" />
      </LoadingState>
    )

  const hierarchy = data.summary.hierarchy ?? []
  const kpis = [
    { label: 'TOTAL', value: agg.total, color: 'var(--color-ink)', hint: '알람 히스토리 보기 →', to: '/alarms?tab=TRACE' },
    { label: 'OOS', value: agg.oos, color: OOS_HEX, hint: 'TRACE 탭 보기 →', to: '/alarms?tab=TRACE' },
    { label: 'OOC', value: agg.ooc, color: OOC_HEX, hint: 'SUMMARY 탭 보기 →', to: '/alarms?tab=SUMMARY' },
    { label: '조치 완료', value: agg.mesSent, color: BLUE_HEX, hint: 'Agent 분석 · 승인 보기 →', to: '/agent-runs' },
  ]

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-16 items-center justify-between pb-1.5 pt-3.5">
        <div className="text-[20px] font-extrabold text-ink">알람 대시보드</div>
        <div className="text-[11.5px] text-g2">
          기간 <span className="font-mono">{applied.from} ~ {applied.to}</span> · 알람{' '}
          <span className="font-mono">{agg.total}</span>건
        </div>
      </div>

      <ScopeFilterBar
        hierarchy={hierarchy}
        draft={draft}
        onDraft={setDraft}
        onApply={() => setApplied(draft)}
        onReset={() => {
          setDraft(DEFAULT_SCOPE)
          setApplied(DEFAULT_SCOPE)
        }}
      />

      <div className="grid grid-cols-4 gap-3">
        {kpis.map((k) => (
          <KpiCard key={k.label} label={k.label} value={k.value} color={k.color} hint={k.hint} onClick={() => navigate(k.to)} />
        ))}
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4">
        <ChartCard title="일자별 알람 추이" note="OOS · OOC">
          <TrendLine data={agg.daily} />
          <LegendRow items={[{ label: 'OOS', color: OOS_HEX }, { label: 'OOC', color: OOC_HEX }]} />
        </ChartCard>
        <ChartCard title="챔버별 알람" note="OOS + OOC 누적">
          <StackBars data={agg.byChamber} />
          <LegendRow items={[{ label: 'OOS', color: OOS_HEX }, { label: 'OOC', color: OOC_HEX }]} />
        </ChartCard>
        <ChartCard title="설비별 알람" note="OOS + OOC 누적">
          <StackBars data={agg.byEquipment} />
          <LegendRow items={[{ label: 'OOS', color: OOS_HEX }, { label: 'OOC', color: OOC_HEX }]} />
        </ChartCard>
        <ChartCard title="파라미터별 알람" note="OOS + OOC 누적">
          <StackBars data={agg.bySensor} />
          <LegendRow items={[{ label: 'OOS', color: OOS_HEX }, { label: 'OOC', color: OOC_HEX }]} />
        </ChartCard>
        <ChartCard title="OOS / OOC 비율">
          <Donut slices={agg.ratio} />
        </ChartCard>
        <ChartCard title="Fault 분류" note="Agent 실행 기준">
          <Donut slices={agg.faults} />
        </ChartCard>
        <ChartCard title="조치별 분포">
          <Donut slices={agg.actionSlices} />
        </ChartCard>
        <ChartCard title="알림 발송" note="채널별 SENT · 미발송">
          <ValueBars data={agg.notify} />
        </ChartCard>
      </div>
    </div>
  )
}

export default DashboardPage

import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getAlarms, getDashboard } from '../../../shared/api/detection.js'
import { getApprovals, getActions, getRuns } from '../../../shared/api/agent.js'
import LoadingState, { Skeleton } from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import { FilterBar, FilterField, FilterSelect, FilterStatic } from '../../../shared/components/ui/FilterField.jsx'
import DashActionBand from '../components/DashActionBand.jsx'
import DashTrendChart from '../components/DashTrendChart.jsx'
import DashParamCard from '../components/DashParamCard.jsx'
import DashEquipCard from '../components/DashEquipCard.jsx'
import DashRecentTable from '../components/DashRecentTable.jsx'

const ALL = '전체'

// 감시 파라미터 8종 (도메인 규칙) — 알람이 없는 종은 '기간 내 이상 없음'으로 표기
const ALL_PARAMS = ['PH_FOCUS', 'PH_DOSE', 'PH_PEB', 'PH_DEV', 'ET_REFL', 'ET_CF4', 'ET_PRES', 'ET_ESC']

const dayLabel = (d) => `${Number(d.slice(5, 7))}/${Number(d.slice(8, 10))}`

function DashboardPage() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  // 계층 필터: 공정 > 장비 > 챔버 — 상위 변경 시 하위 리셋
  const [area, setArea] = useState(ALL)
  const [equipment, setEquipment] = useState(ALL)
  const [chamber, setChamber] = useState(ALL)

  const load = useCallback(() => {
    Promise.all([getAlarms(), getApprovals(), getActions(), getRuns(), getDashboard('2026-06-04', ALL)])
      .then(([alarms, approvals, actions, runs, dashboard]) =>
        setData({
          alarms: alarms.items,
          approvals: approvals.items,
          actions: actions.items,
          runs: runs.items,
          hierarchy: dashboard.hierarchy,
        }),
      )
      .catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    load()
  }, [load])

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
  if (!data)
    return (
      <LoadingState message="대시보드 데이터를 불러오는 중…">
        <Skeleton className="h-[150px]" />
        <Skeleton className="h-[360px]" />
      </LoadingState>
    )

  const { alarms, approvals, actions, runs, hierarchy } = data

  // 설비 → 공정 매핑 (계층 데이터에서 유도)
  const areaOfEqp = {}
  for (const h of hierarchy) for (const e of h.equipments) areaOfEqp[e.id] = h.area

  // 필터 옵션 — 상위 선택으로 하위 옵션을 좁힌다
  const scopedAreas = hierarchy.filter((h) => area === ALL || h.area === area)
  const scopedEqps = scopedAreas.flatMap((h) => h.equipments).filter((e) => equipment === ALL || e.id === equipment)
  const areaOpts = [ALL, ...hierarchy.map((h) => h.area)]
  const eqpOpts = [ALL, ...scopedAreas.flatMap((h) => h.equipments.map((e) => e.id))]
  const chOpts = [ALL, ...scopedEqps.flatMap((e) => e.chambers)]

  const changeArea = (v) => {
    setArea(v)
    setEquipment(ALL)
    setChamber(ALL)
  }
  const changeEquipment = (v) => {
    setEquipment(v)
    setChamber(ALL)
  }

  // 필터 적용 대상: 추이 · 파라미터별 · 설비별 · 최근 알람 (처리 필요 밴드는 전역)
  const filtered = alarms.filter(
    (a) =>
      (area === ALL || areaOfEqp[a.equipment_id] === area) &&
      (equipment === ALL || a.equipment_id === equipment) &&
      (chamber === ALL || a.chamber_id === chamber),
  )

  // 처리 필요 — 승인 대기(requested_at 내림차순) + 실행/전송 실패 건수 집계
  const pendings = approvals
    .filter((p) => p.status === 'PENDING')
    .sort((a, b) => b.requested_at.localeCompare(a.requested_at))
    .map((p) => ({
      ...p,
      // 같은 incident의 R03_CONSEC 알람에서 룰 표기를 유도 (승인이 걸린 근거 룰)
      rule: alarms.find(
        (a) =>
          a.lot_id === p.incident.lot_id &&
          a.chamber_id === p.incident.chamber_id &&
          a.sensor_id === p.incident.sensor_id &&
          a.rule_id === 'R03_CONSEC',
      )?.rule_id,
    }))
  const runFailed = runs.filter((r) => r.status === 'FAILED').length
  const sendFailed = actions.filter((a) => a.send_status === 'FAILED').length

  // 알람 추이 — 일별 judgement 집계. 날짜 축은 전체 데이터 기간으로 고정, 건수는 필터 반영
  const dayKeys = [...new Set(alarms.map((a) => a.occurred_at.slice(0, 10)))].sort()
  const daily = dayKeys.map((d) => {
    const rows = filtered.filter((a) => a.occurred_at.startsWith(d))
    return {
      label: dayLabel(d),
      oos: rows.filter((a) => a.judgement === 'OOS').length,
      ooc: rows.filter((a) => a.judgement === 'OOC').length,
      r03: rows.some((a) => a.rule_id === 'R03_CONSEC'),
    }
  })

  // 파라미터별 집계
  const bySensor = {}
  for (const a of filtered) (bySensor[a.sensor_id] ??= []).push(a)
  const params = Object.entries(bySensor)
    .map(([name, rows]) => ({
      name,
      n: rows.length,
      chambers: [...new Set(rows.map((r) => r.chamber_id))].sort().join(' · '),
    }))
    .sort((x, y) => y.n - x.n || x.name.localeCompare(y.name))
  const quiet = ALL_PARAMS.filter((p) => !bySensor[p])

  // 설비별 집계 — 챔버 목록은 계층 데이터 기준(0건 챔버 포함), 건수 내림차순
  const equips = scopedAreas
    .flatMap((h) => h.equipments)
    .filter((e) => equipment === ALL || e.id === equipment)
    .map((e) => {
      const rows = filtered.filter((a) => a.equipment_id === e.id)
      return {
        id: e.id,
        n: rows.length,
        chambers: e.chambers
          .filter((c) => chamber === ALL || c === chamber)
          .map((c) => ({ id: c, n: rows.filter((a) => a.chamber_id === c).length }))
          .sort((x, y) => y.n - x.n || x.id.localeCompare(y.id)),
      }
    })
    .sort((x, y) => y.n - x.n || x.id.localeCompare(y.id))

  // 최근 알람 — 시각 내림차순 6건 (동시각은 알람 ID 내림차순)
  const recents = [...filtered]
    .sort((a, b) => b.occurred_at.localeCompare(a.occurred_at) || b.alarm_id.localeCompare(a.alarm_id))
    .slice(0, 6)

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-16 items-center justify-between pb-1.5 pt-3.5">
        <div className="text-[22px] font-extrabold text-navy">알람 대시보드</div>
        <FilterBar className="pb-0 pt-0">
          <FilterField label="기간">
            <FilterStatic minWidth={190}>2026-06-01 ~ 06-04</FilterStatic>
          </FilterField>
          <FilterField label="공정">
            <FilterSelect value={area} onChange={changeArea} options={areaOpts} />
          </FilterField>
          <FilterField label="장비">
            <FilterSelect value={equipment} onChange={changeEquipment} options={eqpOpts} />
          </FilterField>
          <FilterField label="챔버">
            <FilterSelect value={chamber} onChange={setChamber} options={chOpts} />
          </FilterField>
        </FilterBar>
      </div>

      <DashActionBand
        pendings={pendings}
        runFailed={runFailed}
        sendFailed={sendFailed}
        onReview={(runId) => navigate(`/agent-runs/${runId}`)}
      />

      <div className="mt-[18px] flex items-stretch gap-5">
        <DashTrendChart daily={daily} />
        <DashParamCard
          params={params}
          quiet={quiet}
          totalKinds={ALL_PARAMS.length}
          onSelect={(sensor) => navigate(`/alarms?sensor=${sensor}`)}
        />
      </div>

      <div className="mt-[18px] flex items-stretch gap-5">
        <DashEquipCard
          equips={equips}
          onSelectChamber={(eqp, ch) => navigate(`/alarms?equipment=${eqp}&chamber=${ch}`)}
        />
        <DashRecentTable recents={recents} />
      </div>
    </div>
  )
}

export default DashboardPage

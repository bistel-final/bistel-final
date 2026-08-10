import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getDashboard } from '../../../shared/api/detection.js'
import { getActions, getRuns } from '../../../shared/api/agent.js'
import LoadingState, { Skeleton } from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import { FilterBar, FilterField, FilterSelect, FilterStatic } from '../../../shared/components/ui/FilterField.jsx'
import DashActionBand from '../components/DashActionBand.jsx'
import DashTrendChart from '../components/DashTrendChart.jsx'
import DashParamCard from '../components/DashParamCard.jsx'
import DashEquipCard from '../components/DashEquipCard.jsx'
import DashRecentTable from '../components/DashRecentTable.jsx'

// 알람 대시보드 — 집계는 전부 서버(GET /dashboard/summary)가 한다.
// 화면은 응답 필드를 그대로 옮겨 그릴 뿐, 알람 목록을 받아 다시 집계하지 않는다.
// 필터(공정·장비·챔버)는 서버 파라미터(area·equipment_id·chamber_id)로 넘어가고
// 로더가 useCallback(deps=필터) 이므로 useEffect 는 로더 호출만 한다.

const ALL = '전체'

// daily_trend.date("2026-06-01") → x 라벨 "6/1"
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
    const scope = {}
    if (area !== ALL) scope.area = area
    if (equipment !== ALL) scope.equipment_id = equipment
    if (chamber !== ALL) scope.chamber_id = chamber
    // 실행 실패·전송 실패는 summary 에 없다 — 각 목록의 total 로 건수만 가져온다
    Promise.all([getDashboard(scope), getRuns({ status: 'FAILED', size: 1 }), getActions({ send_status: 'FAILED', size: 1 })])
      .then(([summary, failedRuns, failedActions]) =>
        setData({ summary, runFailed: failedRuns.total, sendFailed: failedActions.total }),
      )
      .catch((e) => setError(e.message))
  }, [area, equipment, chamber])
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

  const { summary, runFailed, sendFailed } = data
  // hierarchy: [{ area_id, equipment_id, chambers[] }] — 필터 선택지는 항상 전체 계층에서 만든다
  const hierarchy = summary.hierarchy ?? []
  const scopedRows = hierarchy.filter((h) => area === ALL || h.area_id === area)
  const areaOpts = [ALL, ...new Set(hierarchy.map((h) => h.area_id))]
  const eqpOpts = [ALL, ...scopedRows.map((h) => h.equipment_id)]
  const chOpts = [
    ALL,
    ...scopedRows.filter((h) => equipment === ALL || h.equipment_id === equipment).flatMap((h) => h.chambers),
  ]

  const changeArea = (v) => {
    setArea(v)
    setEquipment(ALL)
    setChamber(ALL)
  }
  const changeEquipment = (v) => {
    setEquipment(v)
    setChamber(ALL)
  }

  // 추이 — R03 점선 위치는 has_r03_consec 플래그 그대로 (알람을 뒤져 유도하지 않는다)
  const daily = (summary.daily_trend ?? []).map((d) => ({
    label: dayLabel(d.date),
    oos: d.oos_count,
    ooc: d.ooc_count,
    r03: d.has_r03_consec,
  }))

  // 파라미터별 — 알람 0건인 종은 sensor_catalog 와의 차집합
  const topSensors = summary.top_sensors ?? []
  const sensorCatalog = summary.sensor_catalog ?? []
  const params = topSensors.map((s) => ({
    name: s.sensor_id,
    n: s.alarm_count,
    chambers: (s.chamber_ids ?? []).join(' · '),
  }))
  const quiet = sensorCatalog.filter((s) => !topSensors.some((t) => t.sensor_id === s))

  // 설비별 — 서버가 내려준 순서·건수를 그대로 쓴다 (0건 챔버 포함)
  const equips = (summary.equipment_counts ?? []).map((e) => ({
    id: e.equipment_id,
    n: e.alarm_count,
    chambers: (e.chambers ?? []).map((c) => ({ id: c.chamber_id, n: c.alarm_count })),
  }))

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
        pendings={summary.pending_approvals ?? []}
        runFailed={runFailed}
        sendFailed={sendFailed}
        onReview={(runId) => navigate(`/agent-runs/${runId}`)}
      />

      <div className="mt-[18px] flex items-stretch gap-5">
        <DashTrendChart daily={daily} />
        <DashParamCard
          params={params}
          quiet={quiet}
          totalKinds={sensorCatalog.length}
          onSelect={(sensor) => navigate(`/alarms?sensor=${sensor}`)}
        />
      </div>

      <div className="mt-[18px] flex items-stretch gap-5">
        <DashEquipCard
          equips={equips}
          onSelectChamber={(eqp, ch) => navigate(`/alarms?equipment=${eqp}&chamber=${ch}`)}
        />
        <DashRecentTable recents={summary.recent_alarms ?? []} />
      </div>
    </div>
  )
}

export default DashboardPage

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { getDashboard } from '../../../shared/api/detection.js'
<<<<<<< Updated upstream
import { getActions, getRuns } from '../../../shared/api/agent.js'
import LoadingState, { Skeleton } from '../../../shared/components/LoadingState.jsx'
=======
>>>>>>> Stashed changes
import ErrorState from '../../../shared/components/ErrorState.jsx'
import LoadingState, { Skeleton } from '../../../shared/components/LoadingState.jsx'
import { FilterBar, FilterField, FilterSelect } from '../../../shared/components/ui/FilterField.jsx'
import DashActionBand from '../components/DashActionBand.jsx'
import DashEquipCard from '../components/DashEquipCard.jsx'
import DashParamCard from '../components/DashParamCard.jsx'
import DashRecentTable from '../components/DashRecentTable.jsx'
import DashTrendChart from '../components/DashTrendChart.jsx'

// 알람 대시보드 — 집계는 전부 서버(GET /dashboard/summary)가 한다.
// 화면은 응답 필드를 그대로 옮겨 그릴 뿐, 알람 목록을 받아 다시 집계하지 않는다.
// 필터(공정·장비·챔버)는 서버 파라미터(area·equipment_id·chamber_id)로 넘어가고
// 로더가 useCallback(deps=필터) 이므로 useEffect 는 로더 호출만 한다.

const ALL = '전체'
<<<<<<< Updated upstream

// daily_trend.date("2026-06-01") → x 라벨 "6/1"
const dayLabel = (d) => `${Number(d.slice(5, 7))}/${Number(d.slice(8, 10))}`
=======
const DATE_CLS = 'h-9 rounded-lg border border-line bg-white px-2.5 font-mono text-[12.5px] font-semibold text-navy'
const dayLabel = (date) => `${Number(date.slice(5, 7))}/${Number(date.slice(8, 10))}`
>>>>>>> Stashed changes

function DashboardPage() {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [area, setArea] = useState(ALL)
  const [equipment, setEquipment] = useState(ALL)
  const [chamber, setChamber] = useState(ALL)
  // null은 "API 기본 범위 사용", 빈 문자열은 사용자가 해당 경계를 지운 상태다.
  const [dateFrom, setDateFrom] = useState(null)
  const [dateTo, setDateTo] = useState(null)

  const load = useCallback(() => {
<<<<<<< Updated upstream
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
=======
    const filter = {
      ...(area === ALL ? {} : { area }),
      ...(equipment === ALL ? {} : { equipment_id: equipment }),
      ...(chamber === ALL ? {} : { chamber_id: chamber }),
      ...(dateFrom ? { date_from: dateFrom } : {}),
      ...(dateTo ? { date_to: dateTo } : {}),
    }
    getDashboard(filter)
      .then((response) => {
        setData(response)
        setError(null)
      })
      .catch((requestError) => setError(requestError.message))
  }, [area, chamber, dateFrom, dateTo, equipment])

>>>>>>> Stashed changes
  useEffect(() => {
    load()
  }, [load])

  const hierarchy = useMemo(() => data?.hierarchy ?? [], [data])
  const areaOptions = useMemo(() => [ALL, ...new Set(hierarchy.map((node) => node.area_id))], [hierarchy])
  const equipmentOptions = useMemo(
    () => [
      ALL,
      ...new Set(
        hierarchy.filter((node) => area === ALL || node.area_id === area).map((node) => node.equipment_id),
      ),
    ],
    [area, hierarchy],
  )
  const chamberOptions = useMemo(
    () => [
      ALL,
      ...new Set(
        hierarchy
          .filter((node) => area === ALL || node.area_id === area)
          .filter((node) => equipment === ALL || node.equipment_id === equipment)
          .flatMap((node) => node.chambers),
      ),
    ],
    [area, equipment, hierarchy],
  )

  const changeArea = (value) => {
    setArea(value)
    setEquipment(ALL)
    setChamber(ALL)
  }
  const changeEquipment = (value) => {
    setEquipment(value)
    setChamber(ALL)
  }

  if (error && !data) return <ErrorState detail={error} onRetry={load} />
  if (!data)
    return (
      <LoadingState message="대시보드 데이터를 불러오는 중…">
        <Skeleton className="h-[150px]" />
        <Skeleton className="h-[360px]" />
      </LoadingState>
    )

<<<<<<< Updated upstream
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
=======
  const daily = (data.daily_trend ?? []).map((item) => ({
    label: dayLabel(item.date),
    oos: item.oos_count,
    ooc: item.ooc_count,
    r03: item.has_r03_consec,
  }))
  const params = (data.top_sensors ?? []).map((item) => ({
    name: item.sensor_id,
    n: item.alarm_count,
    chambers: item.chamber_ids.join(' · '),
  }))
  const observed = new Set(params.map((item) => item.name))
  const quiet = (data.sensor_catalog ?? []).filter((sensor) => !observed.has(sensor))
  const equips = (data.equipment_counts ?? []).map((item) => ({
    id: item.equipment_id,
    n: item.alarm_count,
    chambers: item.chambers.map((entry) => ({ id: entry.chamber_id, n: entry.alarm_count })),
  }))
  const pendings = (data.pending_approvals ?? []).map((item) => ({ ...item, rule: item.rule_id }))
  const period = data.date_range?.length === 2 ? `${data.date_range[0]} ~ ${data.date_range[1]}` : data.reference_date
>>>>>>> Stashed changes

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-16 items-center justify-between pb-1.5 pt-3.5">
        <div>
          <div className="text-[22px] font-extrabold text-navy">알람 대시보드</div>
          <div className="mt-1 text-xs text-g1">
            조회 기간 {period ?? '—'} · 기준일 {data.reference_date ?? '—'} · 알람 {data.alarm_count}건 · OOS{' '}
            {data.oos_count} · OOC {data.ooc_count}
          </div>
        </div>
        <FilterBar className="pb-0 pt-0">
          <FilterField label="기간">
            <div className="flex items-center gap-1.5">
              <input
                type="date"
                value={dateFrom ?? data.date_range?.[0] ?? ''}
                max={dateTo || undefined}
                onChange={(event) => setDateFrom(event.target.value)}
                className={DATE_CLS}
                aria-label="대시보드 시작일"
              />
              <span className="text-[13px] font-bold text-g2">~</span>
              <input
                type="date"
                value={dateTo ?? data.date_range?.[1] ?? ''}
                min={dateFrom || undefined}
                onChange={(event) => setDateTo(event.target.value)}
                className={DATE_CLS}
                aria-label="대시보드 종료일"
              />
            </div>
          </FilterField>
          <FilterField label="공정">
            <FilterSelect value={area} onChange={changeArea} options={areaOptions} />
          </FilterField>
          <FilterField label="장비">
            <FilterSelect value={equipment} onChange={changeEquipment} options={equipmentOptions} />
          </FilterField>
          <FilterField label="챔버">
            <FilterSelect value={chamber} onChange={setChamber} options={chamberOptions} />
          </FilterField>
        </FilterBar>
      </div>

      <DashActionBand
<<<<<<< Updated upstream
        pendings={summary.pending_approvals ?? []}
        runFailed={runFailed}
        sendFailed={sendFailed}
=======
        pendings={pendings}
>>>>>>> Stashed changes
        onReview={(runId) => navigate(`/agent-runs/${runId}`)}
      />

      <div className="mt-[18px] flex items-stretch gap-5">
        <DashTrendChart daily={daily} />
        <DashParamCard
          params={params}
          quiet={quiet}
<<<<<<< Updated upstream
          totalKinds={sensorCatalog.length}
=======
          totalKinds={data.sensor_catalog?.length ?? params.length}
>>>>>>> Stashed changes
          onSelect={(sensor) => navigate(`/alarms?sensor=${sensor}`)}
        />
      </div>

      <div className="mt-[18px] flex items-stretch gap-5">
        <DashEquipCard
          equips={equips}
          onSelectChamber={(equipmentId, chamberId) =>
            navigate(`/alarms?equipment=${equipmentId}&chamber=${chamberId}`)
          }
        />
<<<<<<< Updated upstream
        <DashRecentTable recents={summary.recent_alarms ?? []} />
=======
        <DashRecentTable recents={(data.recent_alarms ?? []).slice(0, 5)} />
>>>>>>> Stashed changes
      </div>
    </div>
  )
}

export default DashboardPage

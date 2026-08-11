import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getAlarms, getTraceCatalog, searchTraces } from '../../../shared/api/detection.js'
<<<<<<< Updated upstream
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
=======
>>>>>>> Stashed changes
import EmptyState from '../../../shared/components/EmptyState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import TraceChart from '../components/TraceChart.jsx'
import TraceFilterBar from '../components/TraceFilterBar.jsx'
import { normalizeTraceFilters } from '../components/TraceFilters.js'
import {
  alarmMarkers,
  buildSeries,
  decorateWafers,
<<<<<<< Updated upstream
  resolveFilters,
  resolveScope,
=======
  limitMap,
>>>>>>> Stashed changes
  scopedAlarms,
  searchBodyOf,
  selectRows,
  sensorLimit,
  stepOrderOf,
  stepStats,
} from '../components/TraceModel.jsx'
import TraceSidePanel from '../components/TraceSidePanel.jsx'

<<<<<<< Updated upstream
// 트레이스 뷰어 — 시안 v2 03_트레이스뷰어
// - 필터 선택지는 GET /traces/catalog (areas/equipments/sensors/recipes/lots) 에서 만든다.
// - 파형은 POST /traces/search 1회 호출로 받는다 (웨이퍼별 개별 호출 금지).
// - 한계선은 전역 상수가 아니라 응답 limits[sensor_id] (없으면 catalog.sensors) 에서 센서별로 읽는다.
// - 필터 상태는 전부 쿼리스트링에 직렬화한다 (새로고침·링크 공유 시 동일 화면 복원)
// - 알람 상세의 "크게 보기"는 ?area=&equipment=&chamber=&sensor=&lot=&wafer= 로 진입한다.
//   이때 singular `wafer` 는 "선택"이 아니라 "강조" 지시다 — 웨이퍼는 전 장을 그리고 해당 장만 강조한다.
// - 선택 상태 직렬화에는 복수형 키(sensors / wafers)를 쓴다.

const split = (v) => (v ? v.split(',').map((s) => s.trim()).filter(Boolean) : [])
const isR03 = (a) => String(a.rule_id).startsWith('R03')
// 조회 구간의 알람 — 챔버 단위로 받아 화면에서 LOT·파라미터·웨이퍼로 좁힌다
const ALARM_PAGE_SIZE = 200
=======
const split = (value) => (value ? value.split(',').map((item) => item.trim()).filter(Boolean) : [])
const isR03 = (alarm) => String(alarm.rule_id).startsWith('R03')

const toLegacyRows = (wafers) =>
  decorateWafers(
    (wafers ?? []).map((wafer) => {
      const steps = {}
      for (const point of wafer.points ?? []) {
        const step = point.recipe_step_name ?? `STEP-${point.recipe_step_no ?? 'UNKNOWN'}`
        ;(steps[step] ??= { points: [] }).points.push(point.value)
      }
      return {
        ...wafer,
        wafer_no: String(wafer.wafer_no),
        occurred_at: wafer.occurred_at ?? wafer.points?.find((point) => point.measured_at)?.measured_at ?? null,
        steps,
      }
    }),
  )

const toLimitList = (limits = {}) => {
  const rows = [
    ['USL', limits.spec_upper],
    ['UCL', limits.ctrl_upper],
    ['TARGET', limits.target],
    ['LCL', limits.ctrl_lower],
    ['LSL', limits.spec_lower],
  ]
    .filter(([, value]) => value != null)
    .map(([label, value]) => ({ label, value }))
  return rows.length ? rows : [{ label: 'TARGET', value: 0 }]
}

const measuredStatsFor = (items, sensor) =>
  (items ?? [])
    .filter((item) => item.sensor_id === sensor)
    .map((item) => ({
      step: `${item.recipe_step_name ?? `STEP-${item.recipe_step_no}`} · ${item.lot_hist_id}`,
      source: 'measured',
      mean: item.value_mean,
      std: item.value_std,
      min: item.value_min,
      max: item.value_max,
      n: item.point_cnt,
      wafers: 1,
      judgement: item.judgement,
    }))
>>>>>>> Stashed changes

function TracePage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [catalog, setCatalog] = useState(null)
  const [result, setResult] = useState(null)
<<<<<<< Updated upstream
  const [alarmRes, setAlarmRes] = useState(null)
  const [error, setError] = useState(null)

  const loadCatalog = useCallback(() => {
    getTraceCatalog()
      .then(setCatalog)
      .catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    loadCatalog()
  }, [loadCatalog])

=======
  const [alarms, setAlarms] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

>>>>>>> Stashed changes
  const raw = useMemo(
    () => ({
      area: searchParams.get('area') ?? 'ETCH',
      equipment: searchParams.get('equipment') ?? '',
      chamber: searchParams.get('chamber') ?? '',
<<<<<<< Updated upstream
      sensors: split(searchParams.get('sensors') ?? searchParams.get('sensor')),
=======
      sensors: split(searchParams.get('sensors') ?? searchParams.get('sensor') ?? 'ET_REFL'),
>>>>>>> Stashed changes
      recipe: searchParams.get('recipe') ?? '',
      lot: searchParams.get('lot') ?? '',
      wafers: split(searchParams.get('wafers')),
      from: searchParams.get('from') ?? '',
      to: searchParams.get('to') ?? '',
    }),
    [searchParams],
  )
<<<<<<< Updated upstream

  // 요청 파라미터는 catalog + 쿼리스트링만으로 정한다 (응답에서 유도하면 재조회 루프가 생긴다)
  const scope = useMemo(() => (catalog ? resolveScope(catalog, raw) : null), [catalog, raw])

  const loadTraces = useCallback(() => {
    if (!scope) return
    Promise.all([searchTraces(searchBodyOf(scope)), getAlarms({ chamber_id: scope.chamber, size: ALARM_PAGE_SIZE })])
      .then(([res, alarms]) => {
        setResult(res)
        setAlarmRes(alarms)
      })
      .catch((e) => setError(e.message))
  }, [scope])
  useEffect(() => {
    loadTraces()
  }, [loadTraces])

  const rows = useMemo(() => decorateWafers(result?.wafers, catalog), [result, catalog])
  const alarms = useMemo(() => alarmRes?.items ?? [], [alarmRes])
  const f = useMemo(() => resolveFilters(catalog, rows, raw), [catalog, rows, raw])
  // 스텝 순서는 recipe_step_no 에서 유도한다 (trace 포인트 우선, 알람으로 보완)
  const stepOrder = useMemo(
    () => stepOrderOf([...rows.flatMap((r) => r.points ?? []), ...alarms]),
    [rows, alarms],
  )

  const apply = (next) => {
    const sp = new URLSearchParams()
    sp.set('area', next.area)
    sp.set('equipment', next.equipment)
    sp.set('chamber', next.chamber)
    sp.set('sensors', next.sensors.join(','))
    sp.set('recipe', next.recipe)
    sp.set('lot', next.lot)
    sp.set('wafers', next.wafers.join(','))
    sp.set('from', next.from)
    sp.set('to', next.to)
    const entryWafer = searchParams.get('wafer')
    if (entryWafer) sp.set('wafer', entryWafer)
    setSearchParams(sp)
=======
  const filters = useMemo(() => (catalog ? normalizeTraceFilters(catalog, raw) : null), [catalog, raw])

  const loadCatalog = useCallback(() => {
    getTraceCatalog()
      .then((response) => {
        setCatalog(response)
        setError(null)
      })
      .catch((requestError) => {
        setError(requestError.message)
        setLoading(false)
      })
  }, [])

  useEffect(() => {
    loadCatalog()
  }, [loadCatalog])

  const execute = useCallback(() => {
    if (!filters) return
    const body = {
      area: filters.area || undefined,
      equipment_id: filters.equipment || undefined,
      chamber_id: filters.chamber || undefined,
      sensor_ids: filters.sensors,
      recipe_id: filters.recipe || undefined,
      lot_id: filters.lot || undefined,
      wafer_nos: filters.wafers.map(Number),
      from: filters.from ? `${filters.from}T00:00:00+09:00` : undefined,
      to: filters.to ? `${filters.to}T23:59:59+09:00` : undefined,
    }
    const alarmFilter = {
      chamber_id: filters.chamber || undefined,
      date_from: filters.from || undefined,
      date_to: filters.to || undefined,
      size: 100,
    }
    return Promise.all([searchTraces(body), getAlarms(alarmFilter)])
      .then(([traceResponse, alarmResponse]) => {
        setResult(traceResponse)
        setAlarms(
          (alarmResponse.items ?? []).map((alarm) => ({ ...alarm, wafer_no: String(alarm.wafer_no) })),
        )
        setError(null)
      })
      .catch((requestError) => setError(requestError.message))
      .finally(() => setLoading(false))
  }, [filters])

  useEffect(() => {
    execute()
  }, [execute])

  const apply = (next) => {
    const params = new URLSearchParams()
    params.set('area', next.area)
    params.set('equipment', next.equipment)
    params.set('chamber', next.chamber)
    params.set('sensors', next.sensors.join(','))
    if (next.recipe) params.set('recipe', next.recipe)
    params.set('lot', next.lot)
    params.set('wafers', next.wafers.join(','))
    if (next.from) params.set('from', next.from)
    if (next.to) params.set('to', next.to)
    const focusWafer = searchParams.get('wafer')
    if (focusWafer) params.set('wafer', focusWafer)
    setLoading(true)
    if (params.toString() === searchParams.toString()) execute()
    else setSearchParams(params)
>>>>>>> Stashed changes
  }

  const clearFocus = () => {
    const params = new URLSearchParams(searchParams)
    params.delete('wafer')
    setSearchParams(params)
  }

<<<<<<< Updated upstream
  const retry = () => {
    setError(null)
    setCatalog(null)
    setResult(null)
    setAlarmRes(null)
    loadCatalog()
  }

  if (error) return <ErrorState detail={error} onRetry={retry} />
  if (!catalog || !result || !alarmRes) return <LoadingState message="트레이스 데이터를 불러오는 중…" />

  // 센서별 한계선 — 응답 limits 우선, 없으면 catalog.sensors (PH_DOSE 처럼 trace 없는 센서)
  const limitOf = (sensorId) => sensorLimit(result.limits, catalog, sensorId)
  const a = result.anomaly ?? catalog.anomaly ?? {}
  const anomaly = { score: a.anomaly_score ?? 0, threshold: a.anomaly_threshold ?? 0 }
  const focus = new Set(split(searchParams.get('wafer')).map(Number).filter((w) => f.wafers.includes(w)))
  const inScope = selectRows(rows, f)
  const scoped = scopedAlarms(alarms, f)

  const charts = f.sensors.map((sensor) => {
    const sRows = selectRows(rows, f, sensor)
    // 알람 시점 세로선은 R03(연속 OOS) 알람 기준 — R01/R02 는 우측 알람 카드에만 나열
    const r03 = scoped.filter((x) => x.sensor_id === sensor && isR03(x))
    return { sensor, series: buildSeries(sRows, stepOrder), markers: alarmMarkers(r03) }
  })
  // 시안 순서: 실측 trace 가 있는 파라미터가 위
  charts.sort((x, y) => (y.series.segments.length > 0 ? 1 : 0) - (x.series.segments.length > 0 ? 1 : 0))

  const statsBySensor = f.sensors.map((sensor) => {
    const sRows = selectRows(rows, f, sensor)
    return {
      sensor,
      stats: stepStats(sRows, {
        lot: f.lot,
        chamber: f.chamber,
        sensor,
        waferNos: sRows.map((r) => r.wafer_no),
        measuredStepStats: result.measured_step_stats,
        stepOrder,
        lim: limitOf(sensor),
      }),
=======
  if (error && !result)
    return (
      <ErrorState
        detail={error}
        onRetry={() => {
          setLoading(true)
          if (catalog) execute()
          else loadCatalog()
        }}
      />
    )
  if (!catalog || !filters || loading) return <LoadingState message="트레이스 데이터를 불러오는 중…" />

  const rows = toLegacyRows(result?.wafers)
  const scoped = scopedAlarms(alarms, filters)
  const stepOrder = stepOrderOf(alarms)
  const focus = new Set(split(searchParams.get('wafer')).filter((wafer) => filters.wafers.includes(wafer)))
  const charts = filters.sensors.map((sensor) => {
    const sensorRows = selectRows(rows, filters, sensor)
    const timedRows = sensorRows.filter((row) => Number.isFinite(Date.parse(row.occurred_at)))
    const sensorAlarms = scoped.filter((alarm) => alarm.sensor_id === sensor && isR03(alarm))
    const limits = toLimitList(result?.limits?.[sensor])
    const lim = limitMap(limits)
    const computedStats = stepStats(timedRows, {
      lot: filters.lot,
      chamber: filters.chamber,
      sensor,
      waferNos: timedRows.map((row) => row.wafer_no),
      measuredStepStats: {},
      stepOrder,
      lim,
    })
    return {
      sensor,
      limits,
      lim,
      series: buildSeries(timedRows, stepOrder),
      markers: alarmMarkers(sensorAlarms),
      r03Keys: new Set(sensorAlarms.map((alarm) => `${alarm.wafer_no}|${alarm.recipe_step_name}`)),
      stats: computedStats.some((stat) => stat.source !== 'none')
        ? computedStats
        : measuredStatsFor(result?.measured_step_stats, sensor),
>>>>>>> Stashed changes
    }
  })
  const firstLimits = charts[0]?.lim ?? {}

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-16 items-center justify-between pb-1.5 pt-3.5">
        <div className="text-[22px] font-extrabold text-navy">트레이스 뷰어</div>
        <div className="flex items-center gap-3">
          {focus.size > 0 && (
            <Button variant="outline" sm onClick={clearFocus}>
              알람 강조 해제 (W{[...focus].join('·')})
            </Button>
          )}
          <div className="text-xs text-g1">카탈로그 조회 후 선택 조건을 POST /traces/search로 조회합니다.</div>
        </div>
      </div>

<<<<<<< Updated upstream
      <TraceFilterBar key={searchParams.toString()} catalog={catalog} rows={rows} value={f} onSearch={apply} />

      {inScope.length === 0 ? (
        <EmptyState
          title="조회 조건에 해당하는 trace 가 없습니다"
          description="AREA·설비·챔버·파라미터·레시피·LOT·기간을 조정한 뒤 조회해 주세요."
        />
=======
      <TraceFilterBar key={searchParams.toString()} catalog={catalog} value={filters} onSearch={apply} />

      {!rows.length ? (
        <EmptyState title="조회 조건에 해당하는 trace가 없습니다" description="조회 조건을 조정한 뒤 다시 조회해 주세요." />
>>>>>>> Stashed changes
      ) : (
        <div className="mt-2 flex items-start gap-5">
          <div className="flex min-w-0 flex-1 flex-col gap-[18px]">
            {charts.map((chart) => (
              <TraceChart
<<<<<<< Updated upstream
                key={c.sensor}
                sensor={c.sensor}
                chamber={f.chamber}
                lot={f.lot}
                series={c.series}
                markers={c.markers}
                lim={limitOf(c.sensor)}
=======
                key={chart.sensor}
                sensor={chart.sensor}
                chamber={filters.chamber}
                lot={filters.lot}
                series={chart.series}
                markers={chart.markers}
                r03Keys={chart.r03Keys}
                limits={chart.limits}
                lim={chart.lim}
>>>>>>> Stashed changes
                focus={focus}
              />
            ))}
          </div>
          <div className="w-[330px] flex-none">
            <TraceSidePanel
<<<<<<< Updated upstream
              statsBySensor={statsBySensor}
              anomaly={anomaly}
              alarms={scoped}
              limitOf={limitOf}
=======
              statsBySensor={charts.map(({ sensor, stats }) => ({ sensor, stats }))}
              anomaly={{ score: null, threshold: catalog.anomaly?.threshold ?? null }}
              alarms={scoped}
              lim={firstLimits}
>>>>>>> Stashed changes
              focus={focus}
            />
          </div>
        </div>
      )}
    </div>
  )
}

export default TracePage

import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getAlarms, getTraceCatalog } from '../../../shared/api/detection.js'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import TraceFilterBar from '../components/TraceFilterBar.jsx'
import TraceChart from '../components/TraceChart.jsx'
import TraceSidePanel from '../components/TraceSidePanel.jsx'
import {
  alarmMarkers,
  buildSeries,
  decorateWafers,
  limitMap,
  resolveFilters,
  scopedAlarms,
  selectRows,
  stepOrderOf,
  stepStats,
} from '../components/TraceModel.jsx'

// 트레이스 뷰어
// - 필터 상태는 전부 쿼리스트링에 직렬화한다 (새로고침·링크 공유 시 동일 화면 복원)
// - 알람 상세의 "크게 보기"는 ?area=&equipment=&chamber=&sensor=&lot=&wafer= 로 진입한다.
//   이때 singular `wafer` 는 "선택"이 아니라 "강조" 지시다 — 웨이퍼는 전 장을 그리고 해당 장만 강조한다.
// - 선택 상태 직렬화에는 복수형 키(sensors / wafers)를 쓴다.

const split = (v) => (v ? v.split(',').map((s) => s.trim()).filter(Boolean) : [])

function TracePage() {
  const [searchParams, setSearchParams] = useSearchParams()
  const [catalog, setCatalog] = useState(null)
  const [alarmRes, setAlarmRes] = useState(null)
  const [error, setError] = useState(null)

  const load = useCallback(() => {
    Promise.all([getTraceCatalog(), getAlarms()])
      .then(([c, a]) => {
        setCatalog(c)
        setAlarmRes(a)
      })
      .catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    load()
  }, [load])

  const rows = useMemo(() => decorateWafers(catalog?.wafers), [catalog])
  const alarms = useMemo(() => alarmRes?.items ?? [], [alarmRes])
  const raw = useMemo(
    () => ({
      area: searchParams.get('area') ?? '',
      equipment: searchParams.get('equipment') ?? '',
      chamber: searchParams.get('chamber') ?? '',
      sensors: split(searchParams.get('sensors') ?? searchParams.get('sensor')),
      lot: searchParams.get('lot') ?? '',
      wafers: split(searchParams.get('wafers')),
      from: searchParams.get('from') ?? '',
      to: searchParams.get('to') ?? '',
    }),
    [searchParams],
  )
  const f = useMemo(() => resolveFilters(rows, raw), [rows, raw])
  const stepOrder = useMemo(() => stepOrderOf(alarms), [alarms])

  const apply = (next) => {
    const sp = new URLSearchParams()
    sp.set('area', next.area)
    sp.set('equipment', next.equipment)
    sp.set('chamber', next.chamber)
    sp.set('sensors', next.sensors.join(','))
    sp.set('lot', next.lot)
    sp.set('wafers', next.wafers.join(','))
    sp.set('from', next.from)
    sp.set('to', next.to)
    const entryWafer = searchParams.get('wafer')
    if (entryWafer) sp.set('wafer', entryWafer)
    setSearchParams(sp)
  }

  const clearFocus = () => {
    const sp = new URLSearchParams(searchParams)
    sp.delete('wafer')
    setSearchParams(sp)
  }

  const retry = () => {
    setError(null)
    setCatalog(null)
    setAlarmRes(null)
    load()
  }

  if (error) return <ErrorState detail={error} onRetry={retry} />
  if (!catalog || !alarmRes) return <LoadingState message="트레이스 데이터를 불러오는 중…" />

  const limits = catalog.limits ?? []
  const lim = limitMap(limits)
  const anomaly = catalog.anomaly ?? { score: 0, threshold: 0 }
  const focus = new Set(split(searchParams.get('wafer')).filter((w) => f.wafers.includes(w)))
  const inScope = selectRows(rows, f)
  const scoped = scopedAlarms(alarms, f)

  const charts = f.sensors.map((sensor) => {
    const sRows = selectRows(rows, f, sensor)
    return {
      sensor,
      series: buildSeries(sRows, stepOrder),
      markers: alarmMarkers(scoped.filter((a) => a.sensor_id === sensor)),
    }
  })

  const statsBySensor = f.sensors.map((sensor) => {
    const sRows = selectRows(rows, f, sensor)
    return {
      sensor,
      stats: stepStats(sRows, {
        lot: f.lot,
        chamber: f.chamber,
        sensor,
        waferNos: sRows.map((r) => r.wafer_no),
        measuredStepStats: catalog.measuredStepStats,
        stepOrder,
        lim,
      }),
    }
  })

  return (
    <div className="flex animate-[om-fadein_.3s_ease-out] flex-col gap-3.5">
      <div className="flex flex-wrap items-baseline gap-3">
        <div className="text-[21px] font-extrabold tracking-[-.3px] text-navy">트레이스 뷰어</div>
        {f.chamber && (
          <div className="font-mono text-[13px] font-bold text-slate">
            {f.area} · {f.equipment} · {f.chamber} · {f.lot} · W{f.wafers.join('·')}
          </div>
        )}
        {focus.size > 0 && (
          <button
            type="button"
            onClick={clearFocus}
            className="ml-auto cursor-pointer rounded-lg border border-line-input bg-white px-3 py-[6px] text-[12px] font-bold text-slate hover:bg-line-soft"
          >
            알람 강조 해제 (W{[...focus].join('·')})
          </button>
        )}
      </div>

      <TraceFilterBar key={searchParams.toString()} rows={rows} value={f} onSearch={apply} />

      {inScope.length === 0 ? (
        <EmptyState
          title="조회 조건에 해당하는 trace 가 없습니다"
          description="AREA·설비·챔버·파라미터·LOT·기간을 조정한 뒤 조회해 주세요."
        />
      ) : (
        <div className="grid grid-cols-[1fr_340px] items-start gap-3.5">
          <div className="flex flex-col gap-3.5">
            {charts.map((c) => (
              <TraceChart
                key={c.sensor}
                sensor={c.sensor}
                series={c.series}
                markers={c.markers}
                limits={limits}
                lim={lim}
                focus={focus}
              />
            ))}
          </div>
          <TraceSidePanel
            statsBySensor={statsBySensor}
            anomaly={anomaly}
            alarms={scoped}
            lim={lim}
            focus={focus}
          />
        </div>
      )}
    </div>
  )
}

export default TracePage

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { getAlarms, getTraceCatalog } from '../../../shared/api/detection.js'
import { fmtShort } from '../../../shared/api/format.js'
import StatusBadge from '../../../shared/components/StatusBadge.jsx'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import AlarmHierarchyFilter from '../components/AlarmHierarchyFilter.jsx'
import AlarmDetailPanel from '../components/AlarmDetailPanel.jsx'

const ALL = '전체'
const PAGE_SIZE = 12

// 설비 prefix → 공정(AREA). 알람 데이터에서 계층을 유도한다 (PHO-01-C1 → PHO-01 → PHOTO)
const AREA_BY_PREFIX = { PHO: 'PHOTO', ETC: 'ETCH' }
const areaOf = (a) => AREA_BY_PREFIX[String(a.equipment_id ?? a.chamber_id ?? '').slice(0, 3)] ?? '기타'

const COLUMNS = [
  { key: 'alarm_id', label: '알람', w: '86px' },
  { key: 'occurred_at', label: '시각', w: '104px' },
  { key: 'lot_id', label: 'LOT', w: '96px' },
  { key: 'wafer_no', label: 'W', w: '32px', num: true },
  { key: 'chamber_id', label: '챔버', w: '88px' },
  { key: 'sensor_id', label: '파라미터', w: '84px' },
  { key: 'rule_id', label: '룰', w: '96px' },
  { key: 'judgement', label: '타입', w: '54px' },
  { key: 'action_id', label: '조치', w: '84px' },
]
const GRID = COLUMNS.map((c) => c.w).join(' ')

// R03_CONSEC만 적색 강조, R01/R02는 회색 계열
const ruleTone = (r) => (r === 'R03_CONSEC' ? 'oos' : 'neutral')
const judTone = (j) => (j === 'OOS' ? 'oos' : 'ooc')

const cmp = (a, b, col) => {
  const x = a[col.key] ?? ''
  const y = b[col.key] ?? ''
  if (col.num) return Number(x) - Number(y)
  return String(x).localeCompare(String(y))
}

function AlarmsPage() {
  const { alarmId } = useParams()
  const navigate = useNavigate()

  const [data, setData] = useState(null)
  const [catalog, setCatalog] = useState(null)
  const [error, setError] = useState(null)
  const [filter, setFilter] = useState({ area: ALL, equipment: ALL, chamber: ALL, sensor: ALL })
  const [sort, setSort] = useState({ key: 'occurred_at', dir: 'desc' })
  const [pageOverride, setPageOverride] = useState(null)

  const load = useCallback(() => {
    Promise.all([getAlarms(), getTraceCatalog()])
      .then(([alarmRes, traceRes]) => {
        setData(alarmRes)
        setCatalog(traceRes)
      })
      .catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    load()
  }, [load])

  const retry = () => {
    setError(null)
    setData(null)
    setCatalog(null)
    load()
  }

  const items = useMemo(() => data?.items ?? [], [data])

  const filtered = useMemo(
    () =>
      items.filter(
        (a) =>
          (filter.area === ALL || areaOf(a) === filter.area) &&
          (filter.equipment === ALL || a.equipment_id === filter.equipment) &&
          (filter.chamber === ALL || a.chamber_id === filter.chamber) &&
          (filter.sensor === ALL || a.sensor_id === filter.sensor),
      ),
    [items, filter],
  )

  const sorted = useMemo(() => {
    const col = COLUMNS.find((c) => c.key === sort.key) ?? COLUMNS[1]
    const sign = sort.dir === 'asc' ? 1 : -1
    return [...filtered].sort((a, b) => sign * cmp(a, b, col) || a.alarm_id.localeCompare(b.alarm_id))
  }, [filtered, sort])

  // 상세 패널은 URL(/alarms/:alarmId)에서 복원된다 — 필터와 무관하게 전체 목록에서 찾는다
  const selected = alarmId ? (items.find((a) => a.alarm_id === alarmId) ?? null) : null

  const pageCnt = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE))
  // 페이지를 직접 누르기 전까지는 선택된 알람이 보이는 페이지를 자동으로 맞춘다
  const selIdx = selected ? sorted.findIndex((a) => a.alarm_id === selected.alarm_id) : -1
  const autoPage = selIdx >= 0 ? Math.floor(selIdx / PAGE_SIZE) + 1 : 1
  const curPage = Math.min(pageOverride ?? autoPage, pageCnt)
  const rows = sorted.slice((curPage - 1) * PAGE_SIZE, curPage * PAGE_SIZE)

  const onFilterChange = (key, value) => {
    setPageOverride(null)
    if (key === 'reset') {
      setFilter({ area: ALL, equipment: ALL, chamber: ALL, sensor: ALL })
      return
    }
    // 상위를 바꾸면 하위 선택을 초기화한다
    setFilter((prev) => {
      const next = { ...prev, [key]: value }
      if (key === 'area') return { ...next, equipment: ALL, chamber: ALL, sensor: ALL }
      if (key === 'equipment') return { ...next, chamber: ALL, sensor: ALL }
      if (key === 'chamber') return { ...next, sensor: ALL }
      return next
    })
  }

  const toggleSort = (key) => {
    setPageOverride(null)
    setSort((prev) => (prev.key === key ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' }))
  }

  const select = (id) => navigate(`/alarms/${id}`)
  const close = () => navigate('/alarms')
  const jump = (id) => {
    setPageOverride(null)
    navigate(`/alarms/${id}`)
  }

  if (error) return <ErrorState detail={error} onRetry={retry} />
  if (!data || !catalog) return <LoadingState message="알람 목록을 불러오는 중…" />

  return (
    <div className="flex animate-[om-fadein_.3s_ease-out] flex-col gap-3.5">
      <div className="text-[21px] font-extrabold tracking-[-.3px] text-navy">알람 목록</div>

      <AlarmHierarchyFilter
        alarms={items}
        value={filter}
        onChange={onFilterChange}
        areaOf={areaOf}
        resultCnt={filtered.length}
        totalCnt={data.total ?? items.length}
      />

      <div className={selected ? 'grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_400px]' : 'grid grid-cols-1'}>
        <div className="min-w-0 overflow-hidden rounded-xl border border-line bg-white shadow-[0_1px_3px_rgba(15,42,92,.05)]">
          {sorted.length === 0 ? (
            <EmptyState title="조건에 맞는 알람이 없습니다" description="공정·설비·챔버·파라미터 필터를 조정해 주세요." />
          ) : (
            <div className="overflow-x-auto">
              <div className="min-w-[760px]">
                <div
                  className="grid gap-2 border-b border-line bg-page px-4 py-2.5 text-[12px] font-extrabold text-slate"
                  style={{ gridTemplateColumns: GRID }}
                >
                  {COLUMNS.map((c) => {
                    const on = sort.key === c.key
                    return (
                      <button
                        key={c.key}
                        type="button"
                        onClick={() => toggleSort(c.key)}
                        className={`flex cursor-pointer items-center gap-1 truncate bg-transparent p-0 text-left text-[12px] font-extrabold hover:text-brand ${
                          on ? 'text-navy' : 'text-slate'
                        }`}
                      >
                        {c.label}
                        <span className="text-[9px] leading-none">{on ? (sort.dir === 'asc' ? '▲' : '▼') : '⇅'}</span>
                      </button>
                    )
                  })}
                </div>
                {rows.map((a) => {
                  const on = selected?.alarm_id === a.alarm_id
                  return (
                    <div
                      key={a.alarm_id}
                      onClick={() => select(a.alarm_id)}
                      className="grid cursor-pointer items-center gap-2 border-b border-line-soft px-4 py-2 transition-colors duration-[120ms] hover:bg-line-soft"
                      style={{ gridTemplateColumns: GRID, background: on ? '#DBEAFE' : 'transparent' }}
                    >
                      <span className="truncate font-mono text-[13px] font-extrabold text-navy">{a.alarm_id}</span>
                      <span className="font-mono text-[12.5px] font-semibold text-ink">{fmtShort(a.occurred_at)}</span>
                      <span className="truncate font-mono text-[12.5px] font-semibold text-ink">{a.lot_id}</span>
                      <span className="font-mono text-[12.5px] font-semibold text-ink">{a.wafer_no}</span>
                      <span className="truncate font-mono text-[12.5px] font-semibold text-ink">{a.chamber_id}</span>
                      <span className="truncate font-mono text-[12.5px] font-semibold text-ink">{a.sensor_id}</span>
                      <span className="min-w-0">
                        <StatusBadge tone={ruleTone(a.rule_id)} mono className="text-[10.5px]">
                          {a.rule_id}
                        </StatusBadge>
                      </span>
                      <span className="min-w-0">
                        <StatusBadge tone={judTone(a.judgement)} mono className="text-[10.5px]">
                          {a.judgement}
                        </StatusBadge>
                      </span>
                      <span className="min-w-0 truncate">
                        {a.action_id ? (
                          <Link
                            to={`/actions?action=${a.action_id}`}
                            onClick={(e) => e.stopPropagation()}
                            className="font-mono text-[12.5px] font-extrabold text-brand hover:text-brand-light"
                          >
                            {a.action_id}
                          </Link>
                        ) : (
                          <span className="font-mono text-[12.5px] font-bold text-slate-light">—</span>
                        )}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
          {sorted.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5 px-4 py-3">
              <span className="mr-auto text-[12.5px] font-bold text-slate">
                총 <span className="font-mono font-extrabold text-navy">{sorted.length}</span>건 · {curPage}/{pageCnt}{' '}
                페이지
              </span>
              <button
                type="button"
                disabled={curPage === 1}
                onClick={() => setPageOverride(curPage - 1)}
                className="h-[28px] rounded-[7px] border border-line-input bg-white px-2.5 text-[12.5px] font-extrabold text-slate enabled:cursor-pointer enabled:hover:bg-line-soft disabled:opacity-35"
              >
                ‹
              </button>
              {Array.from({ length: pageCnt }, (_, i) => (
                <button
                  key={i}
                  type="button"
                  onClick={() => setPageOverride(i + 1)}
                  className="h-[28px] w-[28px] cursor-pointer rounded-[7px] border font-mono text-[12.5px] font-extrabold"
                  style={
                    curPage === i + 1
                      ? { background: '#1E5FC2', color: '#FFFFFF', borderColor: '#1E5FC2' }
                      : { background: '#FFFFFF', color: '#475569', borderColor: '#CBD5E1' }
                  }
                >
                  {i + 1}
                </button>
              ))}
              <button
                type="button"
                disabled={curPage === pageCnt}
                onClick={() => setPageOverride(curPage + 1)}
                className="h-[28px] rounded-[7px] border border-line-input bg-white px-2.5 text-[12.5px] font-extrabold text-slate enabled:cursor-pointer enabled:hover:bg-line-soft disabled:opacity-35"
              >
                ›
              </button>
            </div>
          )}
        </div>

        {selected && (
          <AlarmDetailPanel
            alarm={selected}
            alarms={items}
            catalog={catalog}
            area={areaOf(selected)}
            onClose={close}
            onSelect={jump}
          />
        )}
      </div>

      {alarmId && !selected && (
        <div className="rounded-xl border border-line bg-white px-4 py-3 text-[13px] font-semibold text-slate shadow-[0_1px_3px_rgba(15,42,92,.05)]">
          <span className="font-mono font-extrabold text-navy">{alarmId}</span> 알람을 찾을 수 없습니다.{' '}
          <button type="button" onClick={close} className="cursor-pointer font-bold text-brand hover:text-brand-light">
            목록으로
          </button>
        </div>
      )}
    </div>
  )
}

export default AlarmsPage

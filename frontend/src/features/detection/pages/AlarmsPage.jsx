import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
<<<<<<< Updated upstream
import { getAlarm, getAlarms, getTraceCatalog, searchTraces } from '../../../shared/api/detection.js'
import { getRun } from '../../../shared/api/agent.js'
=======
import { createAgentRun } from '../../../shared/api/agent.js'
import { getAlarm, getAlarms, getTraceCatalog } from '../../../shared/api/detection.js'
>>>>>>> Stashed changes
import { fmtShort } from '../../../shared/api/format.js'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import Badge from '../../../shared/components/ui/Badge.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { Card, CardHeader, DashedCard } from '../../../shared/components/ui/Card.jsx'
import Pagination from '../../../shared/components/ui/Pagination.jsx'
import {
  CELL_DIM,
  CELL_ID,
  CELL_MONO,
  TD_CLS,
  TH_CLS,
  judgementClass,
  rowClass,
  ruleVariant,
} from '../../../shared/components/ui/statusStyles.js'
import AlarmHierarchyFilter from '../components/AlarmHierarchyFilter.jsx'
import AlarmDetailPanel from '../components/AlarmDetailPanel.jsx'

const ALL = '전체'
const PAGE_SIZE = 12
// TODO(api): alarm_ids · lot_id 필터 파라미터가 명세에 없다 — 이 두 경우만 넓게 받아 클라이언트에서 좁힌다
const WIDE_SIZE = 200

const COLUMNS = [
  { key: 'alarm_id', label: '알람' },
  { key: 'occurred_at', label: '시각' },
  { key: 'lot_id', label: 'LOT' },
  { key: 'wafer_no', label: 'W', num: true },
  { key: 'chamber_id', label: '챔버' },
  { key: 'sensor_id', label: '파라미터' },
  { key: 'rule_id', label: '룰' },
  { key: 'judgement', label: '타입' },
  { key: 'action_id', label: '조치' },
]

const cmp = (a, b, col) => {
  const x = a[col.key] ?? ''
  const y = b[col.key] ?? ''
  if (col.num) return Number(x) - Number(y)
  return String(x).localeCompare(String(y))
}

const orNull = (v) => (v === ALL ? undefined : v)

function AlarmsPage() {
  const { alarmId } = useParams()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()

  const [list, setList] = useState(null)
  const [catalog, setCatalog] = useState(null)
  const [detail, setDetail] = useState(null)
<<<<<<< Updated upstream
=======
  const [detailError, setDetailError] = useState(null)
  const [creatingRun, setCreatingRun] = useState(false)
>>>>>>> Stashed changes
  const [error, setError] = useState(null)
  // 다른 화면(대시보드 등)이 쿼리 파라미터로 이관한 계층 필터 — 마운트 시 1회만 읽는다
  const [filter, setFilter] = useState(() => ({
    area: searchParams.get('area') ?? ALL,
    equipment: searchParams.get('equipment') ?? ALL,
    chamber: searchParams.get('chamber') ?? ALL,
    sensor: searchParams.get('sensor') ?? ALL,
  }))
  // 조치 목록 '알람 N건'에서 이관한 알람 ID 제한 목록 (?alarms=ALM-0001,ALM-0002,…)
  const [alarmIdFilter, setAlarmIdFilter] = useState(() => {
    const ids = (searchParams.get('alarms') ?? '')
      .split(',')
      .map((s) => s.trim())
      .filter(Boolean)
    return ids.length ? new Set(ids) : null
  })
  const [sort, setSort] = useState({ key: 'occurred_at', dir: 'desc' })
  const [page, setPage] = useState(1)

<<<<<<< Updated upstream
  // 계층 필터는 서버 파라미터로 전달한다 (전량 로드 후 클라이언트 필터 금지)
  const scope = useMemo(
    () => ({
      area: orNull(filter.area),
      equipment_id: orNull(filter.equipment),
      chamber_id: orNull(filter.chamber),
      sensor_id: orNull(filter.sensor),
    }),
    [filter],
  )

  const loadCatalog = useCallback(() => {
    getTraceCatalog()
      .then(setCatalog)
=======
  const load = useCallback(() => {
    Promise.all([getAlarms({ size: 100 }), getTraceCatalog()])
      .then(([alarmRes, traceRes]) => {
        setData(alarmRes)
        setCatalog(traceRes)
      })
>>>>>>> Stashed changes
      .catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    loadCatalog()
  }, [loadCatalog])

  const loadList = useCallback(() => {
    const req = alarmIdFilter
      ? getAlarms({ ...scope, page: 1, size: WIDE_SIZE })
      : getAlarms({ ...scope, page, size: PAGE_SIZE })
    req.then(setList).catch((e) => setError(e.message))
  }, [scope, page, alarmIdFilter])
  useEffect(() => {
    loadList()
  }, [loadList])

  // 상세는 목록 페이지와 무관하게 /alarms/:alarmId 로 복원된다 — 단건 조회로 받는다
  const loadDetail = useCallback(() => {
    if (!alarmId) return
    getAlarm(alarmId)
      .then((alarm) => {
        if (!alarm) return { forId: alarmId, alarm: null }
        return Promise.all([
          // 같은 incident = (lot_id, chamber_id) — chamber_id 로 받아 lot_id 로 좁힌다
          getAlarms({ chamber_id: alarm.chamber_id, size: WIDE_SIZE }),
          searchTraces({
            chamber_id: alarm.chamber_id,
            sensor_ids: [alarm.sensor_id],
            lot_id: alarm.lot_id,
            wafer_nos: [alarm.wafer_no],
          }),
          alarm.latest_agent_run_id ? getRun(alarm.latest_agent_run_id) : Promise.resolve(null),
        ]).then(([sibPage, trace, run]) => ({
          // forId 로 이전 알람의 상세가 남아 보이는 것을 막는다 (effect 안 setState 금지)
          forId: alarmId,
          alarm,
          siblings: (sibPage.items ?? [])
            .filter((s) => s.incident?.lot_id === alarm.incident?.lot_id)
            .sort((a, b) => a.occurred_at.localeCompare(b.occurred_at) || a.alarm_id.localeCompare(b.alarm_id)),
          wafer: trace.wafers?.[0] ?? null,
          limit: trace.limits?.[alarm.sensor_id] ?? null,
          run,
        }))
      })
      .then(setDetail)
      .catch((e) => setError(e.message))
  }, [alarmId])
  useEffect(() => {
    loadDetail()
  }, [loadDetail])

  const areaOf = useMemo(() => {
    const byEquipment = Object.fromEntries((catalog?.equipments ?? []).map((e) => [e.equipment_id, e.area_id]))
    return (a) => byEquipment[a?.equipment_id] ?? ''
  }, [catalog])

  const loadDetail = useCallback(() => {
    if (!alarmId) return
    getAlarm(alarmId)
      .then((alarm) => {
        setDetail({ alarmId, alarm })
        setDetailError(null)
      })
      .catch((requestError) => setDetailError({ alarmId, message: requestError.message }))
  }, [alarmId])

  useEffect(() => {
    loadDetail()
  }, [loadDetail])

  const retry = () => {
    setError(null)
    setList(null)
    setCatalog(null)
    loadCatalog()
    loadList()
    loadDetail()
  }

<<<<<<< Updated upstream
=======
  const items = useMemo(() => data?.items ?? [], [data])

  const filtered = useMemo(
    () =>
      items.filter(
        (a) =>
          (!alarmIdFilter || alarmIdFilter.has(a.alarm_id)) &&
          (filter.area === ALL || areaOf(a) === filter.area) &&
          (filter.equipment === ALL || a.equipment_id === filter.equipment) &&
          (filter.chamber === ALL || a.chamber_id === filter.chamber) &&
          (filter.sensor === ALL || a.sensor_id === filter.sensor),
      ),
    [items, filter, alarmIdFilter],
  )

  const sorted = useMemo(() => {
    const col = COLUMNS.find((c) => c.key === sort.key) ?? COLUMNS[1]
    const sign = sort.dir === 'asc' ? 1 : -1
    return [...filtered].sort((a, b) => sign * cmp(a, b, col) || a.alarm_id.localeCompare(b.alarm_id))
  }, [filtered, sort])

  // 안내 배너 건수 — 계층 필터와 무관하게 ID 제한만 반영한 실제 건수
  const idScopedCnt = useMemo(
    () => (alarmIdFilter ? items.filter((a) => alarmIdFilter.has(a.alarm_id)).length : 0),
    [items, alarmIdFilter],
  )

  // 상세 패널은 목록 페이지와 별개로 GET /alarms/{alarm_id}를 호출해 딥링크에서도 복원한다.
  // `/alarms` 처럼 alarmId 가 없을 때 detail 도 null 이면 undefined === undefined 가 참이 되어
  // null.alarm 을 읽게 된다. detail 존재 여부를 먼저 확인한다.
  const selected = detail && detail.alarmId === alarmId ? detail.alarm : null

  const pageCnt = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE))
  // 페이지를 직접 누르기 전까지는 선택된 알람이 보이는 페이지를 자동으로 맞춘다
  const selIdx = selected ? sorted.findIndex((a) => a.alarm_id === selected.alarm_id) : -1
  const autoPage = selIdx >= 0 ? Math.floor(selIdx / PAGE_SIZE) + 1 : 1
  const curPage = Math.min(pageOverride ?? autoPage, pageCnt)
  const rows = sorted.slice((curPage - 1) * PAGE_SIZE, curPage * PAGE_SIZE)
  const rangeFrom = (curPage - 1) * PAGE_SIZE + 1
  const rangeTo = Math.min(curPage * PAGE_SIZE, sorted.length)

>>>>>>> Stashed changes
  const onFilterChange = (key, value) => {
    setPage(1)
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
    setPage(1)
    setSort((prev) => (prev.key === key ? { key, dir: prev.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' }))
  }

  // 알람 ID 제한 해제 — 전체 목록으로 (URL의 ?alarms=도 함께 지운다)
  const clearAlarmIdFilter = () => {
    setPage(1)
    setAlarmIdFilter(null)
    if (searchParams.has('alarms')) {
      const next = new URLSearchParams(searchParams)
      next.delete('alarms')
      setSearchParams(next, { replace: true })
    }
  }

  const select = (id) => navigate(`/alarms/${id}`)
  const close = () => navigate('/alarms')

  const startAgent = () => {
    if (!selected || creatingRun) return
    setCreatingRun(true)
    createAgentRun(selected.alarm_id)
      .then((accepted) => {
        if (!accepted?.agent_run_id) throw new Error('Agent 실행 ID가 응답에 없습니다.')
        navigate(`/agent-runs/${accepted.agent_run_id}`)
      })
      .catch((requestError) => setDetailError({ alarmId: selected.alarm_id, message: requestError.message }))
      .finally(() => setCreatingRun(false))
  }

  if (error) return <ErrorState detail={error} onRetry={retry} />
  if (!list || !catalog) return <LoadingState message="알람 목록을 불러오는 중…" />

  const items = list.items ?? []
  const scoped = alarmIdFilter ? items.filter((a) => alarmIdFilter.has(a.alarm_id)) : items
  // TODO(api): 정렬 파라미터 미정의 — 서버가 돌려준 페이지를 클라이언트에서 정렬한다
  const col = COLUMNS.find((c) => c.key === sort.key) ?? COLUMNS[1]
  const sign = sort.dir === 'asc' ? 1 : -1
  const sorted = [...scoped].sort((a, b) => sign * cmp(a, b, col) || a.alarm_id.localeCompare(b.alarm_id))

  // 페이저는 서버 total 기준 — ID 제한 모드에서만 클라이언트 건수를 쓴다
  const total = alarmIdFilter ? scoped.length : (list.total ?? scoped.length)
  const pageCnt = Math.max(1, Math.ceil(total / PAGE_SIZE))
  const curPage = Math.min(page, pageCnt)
  const rows = alarmIdFilter ? sorted.slice((curPage - 1) * PAGE_SIZE, curPage * PAGE_SIZE) : sorted
  const rangeFrom = total === 0 ? 0 : (curPage - 1) * PAGE_SIZE + 1
  const rangeTo = Math.min(curPage * PAGE_SIZE, total)

  const shown = detail?.forId === alarmId ? detail : null
  const selected = shown?.alarm ?? null

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-16 items-center justify-between pb-1.5 pt-3.5">
        <div className="text-[22px] font-extrabold text-navy">알람 목록</div>
        <div className="text-xs text-g1">
          기간 내 <span className="font-mono">{total}</span>건
        </div>
      </div>

      <AlarmHierarchyFilter catalog={catalog} value={filter} onChange={onFilterChange} />

      {alarmIdFilter && (
        <div className="mb-3.5 flex items-center gap-3 rounded-lg border border-tint-blue-line bg-tint-blue px-3.5 py-2 text-[12.5px] font-semibold text-navy">
          조치 연관 알람 <span className="font-mono font-bold">{scoped.length}</span>건 표시 중
          <Button variant="outline" sm className="ml-auto" onClick={clearAlarmIdFilter}>
            전체 목록으로
          </Button>
        </div>
      )}

      <div className="flex items-start gap-5">
        <Card className="min-w-0 flex-1">
          <CardHeader title="알람" note="발생 시각 내림차순" />
          {rows.length === 0 ? (
            <EmptyState title="조건에 맞는 알람이 없습니다" description="공정·설비·챔버·파라미터 필터를 조정해 주세요." />
          ) : (
            <div className="overflow-x-auto px-2">
              <table className="w-full border-collapse">
                <thead>
                  <tr>
                    {COLUMNS.map((c) => {
                      const on = sort.key === c.key
                      return (
                        <th
                          key={c.key}
                          onClick={() => toggleSort(c.key)}
                          className={`${TH_CLS} cursor-pointer select-none ${on ? 'text-navy' : ''}`}
                        >
                          {c.label} <span className={`text-[9px] ${on ? 'text-navy' : 'text-g2'}`}>{on && sort.dir === 'asc' ? '▴' : '▾'}</span>
                        </th>
                      )
                    })}
                  </tr>
                </thead>
                <tbody>
                  {rows.map((a, i) => (
                    <tr
                      key={a.alarm_id}
                      onClick={() => select(a.alarm_id)}
                      className={`cursor-pointer ${rowClass(i, { sel: selected?.alarm_id === a.alarm_id })}`}
                    >
                      <td className={`${TD_CLS} ${CELL_ID}`}>{a.alarm_id}</td>
                      <td className={`${TD_CLS} ${CELL_DIM}`}>{fmtShort(a.occurred_at)}</td>
                      <td className={`${TD_CLS} ${CELL_MONO}`}>{a.lot_id}</td>
                      <td className={`${TD_CLS} ${CELL_MONO}`}>{a.wafer_no}</td>
                      <td className={`${TD_CLS} ${CELL_DIM}`}>{a.chamber_id}</td>
                      <td className={`${TD_CLS} ${CELL_MONO} font-semibold`}>{a.sensor_id}</td>
                      <td className={TD_CLS}>
                        <Badge variant={ruleVariant(a.rule_id)}>{String(a.rule_id).slice(0, 3)}</Badge>
                      </td>
                      <td className={`${TD_CLS} ${CELL_MONO} font-bold ${judgementClass(a.judgement)}`}>{a.judgement}</td>
                      <td className={TD_CLS}>
                        {a.action_id ? (
                          <Link
                            to={`/actions?action=${a.action_id}`}
                            onClick={(e) => e.stopPropagation()}
                            className={CELL_MONO}
                          >
                            {a.action_id}
                          </Link>
                        ) : (
                          <span className={`${CELL_MONO} text-g2`}>—</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {total > 0 && (
            <Pagination
              page={curPage}
              pageCount={pageCnt}
              rangeLabel={`${rangeFrom} – ${rangeTo} / ${total}`}
              onPage={setPage}
            />
          )}
        </Card>

        <div className="w-[470px] flex-none">
<<<<<<< Updated upstream
          {selected ? (
            <AlarmDetailPanel
              alarm={selected}
              siblings={shown.siblings}
              wafer={shown.wafer}
              limit={shown.limit}
              run={shown.run}
              area={areaOf(selected)}
              onSelect={select}
            />
          ) : alarmId && !shown ? (
            <LoadingState message="알람 상세를 불러오는 중…" />
          ) : alarmId ? (
=======
          {detailError && detailError.alarmId === alarmId ? (
            <ErrorState title="알람 상세를 불러오지 못했습니다" detail={detailError.message} onRetry={loadDetail} />
          ) : selected ? (
            <AlarmDetailPanel
              alarm={selected}
              alarms={items}
              catalog={catalog}
              area={areaOf(selected)}
              onSelect={jump}
              onStartAgent={startAgent}
              creatingRun={creatingRun}
            />
          ) : detail && detail.alarmId === alarmId ? (
>>>>>>> Stashed changes
            <DashedCard className="flex flex-col items-start gap-3 px-[18px] py-4">
              <div className="text-[13px] font-extrabold text-navy">
                <span className="font-mono">{alarmId}</span> 알람을 찾을 수 없습니다
              </div>
              <Button variant="outline" sm onClick={close}>
                목록으로 →
              </Button>
            </DashedCard>
          ) : alarmId ? (
            <LoadingState message="알람 상세를 불러오는 중…" />
          ) : (
            <DashedCard className="px-[18px] py-4">
              <div className="text-[13px] font-extrabold text-navy">행을 누르면 상세가 열린다</div>
              <div className="mt-2 text-[11.5px] leading-[1.6] text-g1">
                선택한 알람은 <span className="font-mono text-blue">/alarms/:alarmId</span> 주소에 남아
                <br />
                공유되고 뒤로가기가 목록으로 돌아온다.
              </div>
            </DashedCard>
          )}
        </div>
      </div>
    </div>
  )
}

export default AlarmsPage

import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { getActions } from '../../../shared/api/agent.js'
import { getTraceCatalog } from '../../../shared/api/detection.js'
import { fmtShort, isoToParts } from '../../../shared/api/format.js'
import { ACTION_TABS } from '../mock/actions.js'
import { matchTab, sortActions, tabParams } from '../actionsSort.js'
import ActionDetailPanel from '../components/ActionDetailPanel.jsx'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import Badge from '../../../shared/components/ui/Badge.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import Pagination from '../../../shared/components/ui/Pagination.jsx'
import {
  FilterBar,
  FilterField,
  FilterSelect,
  FilterStatic,
} from '../../../shared/components/ui/FilterField.jsx'
import {
  actionCodeVariant,
  approvalClass,
  approvalLabel,
  rowClass,
  severityClass,
  CELL_DIM,
  CELL_ID,
  CELL_MONO,
  CELL_SUB,
  TD_CLS,
  TH_CLS,
} from '../../../shared/components/ui/statusStyles.js'

const ALL = '전체'
const PAGE_SIZE = 20
// 탭 배지용 조회 — 같은 필터의 전체 집합을 한 번 받아 다섯 탭을 센다 (건수 하드코딩 금지)
const COUNT_SIZE = 500

const HEADERS = ['조치', 'incident', '파라미터', '조치 코드', '심각도', '승인', '전송', '알람', '시각', '']

// 조건부 파라미터는 '전체'일 때 키 자체를 빼서 보낸다 (빈 문자열 금지)
const param = (key, value) => (value === ALL ? null : { [key]: value })

function ActionsPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [res, setRes] = useState(null) // 현재 탭·필터의 목록 응답
  const [countRes, setCountRes] = useState(null) // 같은 필터의 전체 집합 (탭 배지)
  const [catalog, setCatalog] = useState(null) // 공정·설비·챔버 계층 (GET /traces/catalog)
  const [codes, setCodes] = useState([]) // 조치 코드 옵션 — 필터와 무관한 전체 집합에서 1회 유도
  const [error, setError] = useState(null)
  // 기본 선택은 '승인 대기'. 단, ?action=... 딥링크로 들어오면 해당 행이 어느 상태든
  // 보이도록 '전체'에서 시작한다 (초기 렌더에서 한 번만 평가)
  const [tab, setTab] = useState(() => (searchParams.get('action') ? 'ALL' : 'PENDING'))
  const [area, setArea] = useState(ALL)
  const [equipment, setEquipment] = useState(ALL)
  const [chamber, setChamber] = useState(ALL)
  const [code, setCode] = useState(ALL)
  const [page, setPage] = useState(1)

  // 조치 코드·설비·챔버는 서버 파라미터로 넘긴다.
  // TODO(api): GET /actions 에 area 파라미터가 없어 공정만 클라이언트에서 거른다
  const filterParams = useMemo(
    () => ({ ...param('action_code', code), ...param('equipment_id', equipment), ...param('chamber_id', chamber) }),
    [code, equipment, chamber],
  )

  // 탭·필터가 바뀌면 load가 새로 만들어져 useEffect가 다시 돈다.
  // setState는 전부 then/catch 안에서만 호출한다 (react-hooks/set-state-in-effect)
  const load = useCallback(() => {
    Promise.all([
      getActions({ ...filterParams, ...tabParams(tab), page, size: PAGE_SIZE }),
      getActions({ ...filterParams, page: 1, size: COUNT_SIZE }),
    ])
      .then(([list, all]) => {
        setRes(list)
        setCountRes(all)
      })
      .catch((e) => setError(e.message))
  }, [filterParams, tab, page])

  // 필터 옵션 소스는 필터와 무관하므로 최초 1회만 받는다
  const loadOptions = useCallback(() => {
    Promise.all([getTraceCatalog(), getActions({ page: 1, size: COUNT_SIZE })])
      .then(([cat, all]) => {
        setCatalog(cat)
        setCodes([...new Set(all.items.map((a) => a.action_code))])
      })
      .catch((e) => setError(e.message))
  }, [])

  useEffect(() => {
    load()
  }, [load])
  useEffect(() => {
    loadOptions()
  }, [loadOptions])

  // 열린 행은 URL에 담는다 — 새로고침·링크 공유 시 그대로 복원된다
  const openId = searchParams.get('action')
  const toggleOpen = (id) => {
    const next = new URLSearchParams(searchParams)
    if (openId === id) next.delete('action')
    else next.set('action', id)
    setSearchParams(next, { replace: true })
  }

  // 공정·설비·챔버 옵션은 설비 마스터에서 유도한다 (챔버 ID 절단 금지 — 라벨은 PHOTO/ETCH)
  const equipments = useMemo(() => catalog?.equipments ?? [], [catalog])
  const areaById = useMemo(
    () => Object.fromEntries(equipments.map((e) => [e.equipment_id, e.area_id])),
    [equipments],
  )
  const inArea = useCallback((e) => area === ALL || e.area_id === area, [area])
  const areaOptions = useMemo(() => [ALL, ...new Set(equipments.map((e) => e.area_id))], [equipments])
  const equipmentOptions = useMemo(
    () => [ALL, ...equipments.filter(inArea).map((e) => e.equipment_id)],
    [equipments, inArea],
  )
  const chamberOptions = useMemo(
    () => [
      ALL,
      ...equipments
        .filter(inArea)
        .filter((e) => equipment === ALL || e.equipment_id === equipment)
        .flatMap((e) => e.chambers),
    ],
    [equipments, inArea, equipment],
  )
  const codeOptions = useMemo(() => [ALL, ...codes], [codes])

  // 상위를 바꾸면 하위 선택을 즉시 되돌린다 (useEffect setState 금지)
  const onArea = (next) => {
    setPage(1)
    setArea(next)
    setEquipment(ALL)
    setChamber(ALL)
  }
  const onEquipment = (next) => {
    setPage(1)
    setEquipment(next)
    setChamber(ALL)
  }
  const onChamber = (next) => {
    setPage(1)
    setChamber(next)
  }
  const onCode = (next) => {
    setPage(1)
    setCode(next)
  }
  const onTab = (next) => {
    setPage(1)
    setTab(next)
  }

  // 공정만 클라이언트 필터 — 서버가 이미 걸러 준 집합 위에 얹는다
  const inSelectedArea = useCallback((a) => area === ALL || areaById[a.equipment_id] === area, [area, areaById])

  const countItems = useMemo(() => (countRes?.items ?? []).filter(inSelectedArea), [countRes, inSelectedArea])
  const counts = useMemo(
    () => Object.fromEntries(ACTION_TABS.map((t) => [t.key, countItems.filter((a) => matchTab(a, t.key)).length])),
    [countItems],
  )

  // 정렬 규칙: PENDING 최상단 → 시각 내림차순
  const rows = useMemo(() => sortActions((res?.items ?? []).filter(inSelectedArea)), [res, inSelectedArea])

  if (error)
    return (
      <ErrorState
        title="조치 목록을 불러오지 못했습니다"
        detail={error}
        onRetry={() => {
          setError(null)
          setRes(null)
          load()
          loadOptions()
        }}
      />
    )
  if (!res || !catalog) return <LoadingState message="조치 목록을 불러오는 중…" />

  // 기간 표시는 데이터의 created_at 범위에서 계산한다 (정적 필터 — 값 창작 금지)
  const dates = countItems.map((a) => isoToParts(a.created_at).date).sort()
  const period = dates.length ? `${dates[0]} ~ ${dates[dates.length - 1].slice(5)}` : '—'
  const total = res.total ?? rows.length
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-[64px] items-center justify-between pb-1.5 pt-3.5">
        <div className="text-[22px] font-extrabold text-navy">조치 목록</div>
        <div className="text-xs text-g1">incident 단위 · 기간 내 {counts.ALL ?? 0}건</div>
      </div>

      <FilterBar className="pt-2">
        <FilterField label="기간">
          <FilterStatic minWidth={190}>{period}</FilterStatic>
        </FilterField>
        <FilterField label="공정">
          <FilterSelect value={area} onChange={onArea} options={areaOptions} />
        </FilterField>
        <FilterField label="설비">
          <FilterSelect value={equipment} onChange={onEquipment} options={equipmentOptions} />
        </FilterField>
        <FilterField label="챔버">
          <FilterSelect value={chamber} onChange={onChamber} options={chamberOptions} />
        </FilterField>
        <FilterField label="조치">
          <FilterSelect value={code} onChange={onCode} options={codeOptions} />
        </FilterField>
      </FilterBar>

      {/* 상태 탭 — 건수는 응답 데이터에서 계산한다 (하드코딩 금지). 활성 탭은 적색 보더+텍스트 */}
      <div className="flex items-center gap-2.5 pb-4 pt-0.5">
        {ACTION_TABS.map((t) => {
          const on = tab === t.key
          return (
            <button
              key={t.key}
              type="button"
              onClick={() => onTab(t.key)}
              className={`inline-flex h-[34px] cursor-pointer items-center gap-2.5 rounded-lg border bg-white px-4 text-[13px] ${
                on ? 'border-red font-bold text-red' : 'border-line font-semibold text-g1'
              }`}
            >
              {t.label} <span className="font-mono text-xs">{counts[t.key] ?? 0}</span>
            </button>
          )
        })}
        <span className="ml-auto text-xs text-g1">기본은 승인 대기 — 사람이 볼 게 먼저</span>
      </div>

      <Card>
        <CardHeader title="조치" note="조치 시각 내림차순" />
        <div className="overflow-x-auto px-3 pb-2">
          <table className="w-full border-collapse">
            <thead>
              <tr>
                {HEADERS.map((h, i) => (
                  <th key={h || `col-${i}`} className={TH_CLS}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 && (
                <tr>
                  <td colSpan={HEADERS.length}>
                    <EmptyState
                      title="해당 상태의 조치가 없습니다"
                      description="다른 상태 탭이나 필터를 선택해 조회해 주세요."
                    />
                  </td>
                </tr>
              )}
              {rows.map((a, i) => {
                const isOpen = openId === a.action_id
                const isPending = a.approval_status === 'PENDING'
                return (
                  <ActionRow
                    key={a.action_id}
                    action={a}
                    cls={rowClass(i, { red: isPending, sel: isOpen })}
                    isOpen={isOpen}
                    isPending={isPending}
                    onToggle={() => toggleOpen(a.action_id)}
                    onReview={() => navigate(`/agent-runs/${a.agent_run_id}`)}
                  />
                )
              })}
            </tbody>
          </table>
        </div>
        {pageCount > 1 && (
          <Pagination
            page={page}
            pageCount={pageCount}
            total={total}
            rangeLabel={`${total}건 중 ${rows.length}건`}
            onPage={setPage}
          />
        )}
      </Card>
    </div>
  )
}

function ActionRow({ action: a, cls, isOpen, isPending, onToggle, onReview }) {
  return (
    <>
      <tr className={`cursor-pointer ${cls}`} onClick={onToggle}>
        <td className={TD_CLS}>
          <div className={CELL_ID}>{a.action_id}</div>
          <div className="mt-[3px]">
            <Link
              to={`/agent-runs/${a.agent_run_id}`}
              onClick={(e) => e.stopPropagation()}
              className="font-mono text-[10.5px]"
            >
              상세 →
            </Link>
          </div>
        </td>
        <td className={TD_CLS}>
          <div className={`${CELL_MONO} font-semibold`}>
            {a.incident.lot_id} · {a.incident.chamber_id}
          </div>
          <div className={CELL_SUB}>LOT · 챔버</div>
        </td>
        <td className={`${TD_CLS} ${CELL_MONO} font-semibold`}>{a.sensor_id}</td>
        <td className={TD_CLS}>
          <Badge variant={actionCodeVariant(a.action_code)}>{a.action_code}</Badge>
        </td>
        <td className={`${TD_CLS} ${CELL_MONO} font-bold ${severityClass(a.severity)}`}>{a.severity}</td>
        <td className={TD_CLS}>
          <span className={`text-xs font-bold ${approvalClass(a.approval_status)}`}>
            {approvalLabel(a.approval_status)}
          </span>
        </td>
        <td className={TD_CLS}>
          {a.send_status === 'SENT' ? <Badge variant="t-green">SENT</Badge> : <span className="text-g2">—</span>}
        </td>
        <td className={TD_CLS}>
          {/* 알람 N건 → 해당 알람들만 필터된 알람 목록으로 이동 */}
          <Link
            to={`/alarms?alarms=${a.alarm_ids.join(',')}`}
            onClick={(e) => e.stopPropagation()}
            className={`${CELL_MONO} font-bold`}
          >
            {a.alarm_count ?? a.alarm_ids.length}건
          </Link>
        </td>
        <td className={`${TD_CLS} ${CELL_DIM}`}>{fmtShort(a.created_at)}</td>
        <td className={`${TD_CLS} text-right`}>
          {isPending && (
            <Button
              sm
              onClick={(e) => {
                e.stopPropagation()
                onReview()
              }}
            >
              검토 →
            </Button>
          )}
        </td>
      </tr>
      {isOpen && (
        <tr>
          <td colSpan={10} className="border-b border-cell-line px-3 pb-3.5 pt-1">
            <ActionDetailPanel actionId={a.action_id} />
          </td>
        </tr>
      )}
    </>
  )
}

export default ActionsPage

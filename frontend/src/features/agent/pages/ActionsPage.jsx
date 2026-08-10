import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { getActions } from '../../../shared/api/agent.js'
import { fmtShort, isoToParts } from '../../../shared/api/format.js'
import { ACTION_TABS } from '../mock/actions.js'
import { sortActions } from '../actionsSort.js'
import ActionDetailPanel from '../components/ActionDetailPanel.jsx'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import Badge from '../../../shared/components/ui/Badge.jsx'
import Button from '../../../shared/components/ui/Button.jsx'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
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

// 탭 판정 — 승인 대기만 approval_status, 나머지는 send_status 기준 (명세)
const matchTab = (a, key) =>
  key === 'ALL' ? true : key === 'PENDING' ? a.approval_status === 'PENDING' : a.send_status === key

// 공정은 챔버 ID 앞 세그먼트(ETC·PHO)로 유도한다 — incident 외 값 창작 없음
const areaOf = (a) => a.incident.chamber_id.split('-')[0]

const HEADERS = ['조치', 'incident', '파라미터', '조치 코드', '심각도', '승인', '전송', '알람', '시각', '']

function ActionsPage() {
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const [actions, setActions] = useState(null)
  const [error, setError] = useState(null)
  // 기본 선택은 '승인 대기'. 단, ?action=... 딥링크로 들어오면 해당 행이 어느 상태든
  // 보이도록 '전체'에서 시작한다 (초기 렌더에서 한 번만 평가)
  const [tab, setTab] = useState(() => (searchParams.get('action') ? 'ALL' : 'PENDING'))
  const [area, setArea] = useState(ALL)
  const [chamber, setChamber] = useState(ALL)
  const [code, setCode] = useState(ALL)

  const load = useCallback(() => {
    getActions()
      .then((res) => setActions(res.items))
      .catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    load()
  }, [load])

  // 열린 행은 URL에 담는다 — 새로고침·링크 공유 시 그대로 복원된다
  const openId = searchParams.get('action')
  const toggleOpen = (id) => {
    const next = new URLSearchParams(searchParams)
    if (openId === id) next.delete('action')
    else next.set('action', id)
    setSearchParams(next, { replace: true })
  }

  const src = useMemo(() => actions ?? [], [actions])

  // 필터 옵션은 incident에서 유도한다 (하드코딩 금지). 설비는 선택 공정에 종속
  const areaOptions = useMemo(() => [ALL, ...new Set(src.map(areaOf))], [src])
  const chamberOptions = useMemo(
    () => [
      ALL,
      ...new Set(src.filter((a) => area === ALL || areaOf(a) === area).map((a) => a.incident.chamber_id)),
    ],
    [src, area],
  )
  const codeOptions = useMemo(() => [ALL, ...new Set(src.map((a) => a.action_code))], [src])

  // 공정 변경 시 무효해진 설비 선택은 핸들러에서 즉시 되돌린다 (useEffect setState 금지)
  const onArea = (next) => {
    setArea(next)
    if (chamber !== ALL && next !== ALL && !chamber.startsWith(next)) setChamber(ALL)
  }

  // 공정·설비·조치 필터 → 탭 건수도 같은 집합 기준으로 센다
  const filtered = useMemo(
    () =>
      src.filter(
        (a) =>
          (area === ALL || areaOf(a) === area) &&
          (chamber === ALL || a.incident.chamber_id === chamber) &&
          (code === ALL || a.action_code === code),
      ),
    [src, area, chamber, code],
  )

  const counts = useMemo(
    () => Object.fromEntries(ACTION_TABS.map((t) => [t.key, filtered.filter((a) => matchTab(a, t.key)).length])),
    [filtered],
  )

  // 정렬은 전체 집합에 한 번 — 탭은 같은 순서에 필터만 얹는다 (규칙: PENDING 최상단 → 시각 내림차순)
  const rows = useMemo(() => sortActions(filtered).filter((a) => matchTab(a, tab)), [filtered, tab])

  if (error)
    return (
      <ErrorState
        title="조치 목록을 불러오지 못했습니다"
        detail={error}
        onRetry={() => {
          setError(null)
          setActions(null)
          load()
        }}
      />
    )
  if (!actions) return <LoadingState message="조치 목록을 불러오는 중…" />

  // 기간 표시는 데이터의 created_at 범위에서 계산한다 (정적 필터 — 값 창작 금지)
  const dates = src.map((a) => isoToParts(a.created_at).date).sort()
  const period = dates.length ? `${dates[0]} ~ ${dates[dates.length - 1].slice(5)}` : '—'

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-[64px] items-center justify-between pb-1.5 pt-3.5">
        <div className="text-[22px] font-extrabold text-navy">조치 목록</div>
        <div className="text-xs text-g1">incident 단위 · 기간 내 {src.length}건</div>
      </div>

      <FilterBar className="pt-2">
        <FilterField label="기간">
          <FilterStatic minWidth={190}>{period}</FilterStatic>
        </FilterField>
        <FilterField label="공정">
          <FilterSelect value={area} onChange={onArea} options={areaOptions} />
        </FilterField>
        <FilterField label="설비">
          <FilterSelect value={chamber} onChange={setChamber} options={chamberOptions} />
        </FilterField>
        <FilterField label="조치">
          <FilterSelect value={code} onChange={setCode} options={codeOptions} />
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
              onClick={() => setTab(t.key)}
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
                    onReview={() => navigate(`/agent-runs/${a.run_id}`)}
                  />
                )
              })}
            </tbody>
          </table>
        </div>
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
              to={`/agent-runs/${a.run_id}`}
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
        <td className={`${TD_CLS} ${CELL_MONO} font-semibold`}>{a.incident.sensor_id}</td>
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

import { useCallback, useEffect, useMemo, useState } from 'react'
import { getAuditLogs } from '../../../shared/api/analytics.js'
import { isoToParts } from '../../../shared/api/format.js'
import AuditFilterBar from '../components/AuditFilterBar.jsx'
import AuditTimeline from '../components/AuditTimeline.jsx'
import AuditEventTypeBars from '../components/AuditEventTypeBars.jsx'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import ErrorState from '../../../shared/components/ErrorState.jsx'

const DEF_FROM = '2026-06-01'
const DEF_TO = '2026-06-04'
const DEF_ACTOR = '전체'

// 시각이 분 단위라 같은 분 안에서는 생애주기 흐름 순서로 정렬한다
// (TODO(data): audit_log 초 단위 확보 시 이 보조 정렬은 제거)
const FLOW_RANK = {
  AGENT_RUN_STARTED: 0,
  APPROVAL_REQUESTED: 1,
  ACTION_APPROVED: 2,
  ACTION_REJECTED: 2,
  ACTION_SEND_STARTED: 3,
  ACTION_SENT: 4,
  ACTION_SEND_FAILED: 4,
  AGENT_RUN_COMPLETED: 5,
  AGENT_RUN_FAILED: 5,
}

const flowRank = (ev) => FLOW_RANK[ev] ?? 9

function AuditLogPage() {
  const [res, setRes] = useState(null)
  const [error, setError] = useState(null)
  const [events, setEvents] = useState([])
  const [actor, setActor] = useState(DEF_ACTOR)
  const [from, setFrom] = useState(DEF_FROM)
  const [to, setTo] = useState(DEF_TO)
  const [target, setTarget] = useState('')
  const [sort, setSort] = useState('asc')

  const load = useCallback(() => {
    getAuditLogs()
      .then(setRes)
      .catch((e) => setError(e.message))
  }, [])
  useEffect(() => {
    load()
  }, [load])

  const retry = () => {
    setError(null)
    setRes(null)
    load()
  }

  const eventTypes = useMemo(() => res?.event_types ?? [], [res])

  // 서버 명세에 대상 ID 필터가 없어 조회 결과 전체에 클라이언트 필터를 적용한다
  const filtered = useMemo(() => {
    const items = res?.items ?? []
    const q = target.trim().toLowerCase()
    const hit = items.filter((e) => {
      const { date } = isoToParts(e.at)
      return (
        (events.length === 0 || events.includes(e.ev)) &&
        (actor === DEF_ACTOR || e.ac === actor) &&
        date >= from &&
        date <= to &&
        // TODO(api): entity_id 파라미터 제안 반영 대기
        (!q || String(e.entity_id).toLowerCase().includes(q))
      )
    })
    const dir = sort === 'asc' ? 1 : -1
    return [...hit].sort((a, b) => dir * (a.at.localeCompare(b.at) || flowRank(a.ev) - flowRank(b.ev)))
  }, [res, events, actor, from, to, target, sort])

  const pristine =
    events.length === 0 && actor === DEF_ACTOR && from === DEF_FROM && to === DEF_TO && target.trim() === ''

  // 집계는 현재 화면 slice가 아니라 같은 필터의 전체 집합 기준이다.
  // 필터가 기본값이면 응답의 event_type_counts를 그대로 사용한다.
  const counts = useMemo(() => {
    if (!res) return {}
    if (pristine) return res.event_type_counts ?? {}
    return Object.fromEntries(eventTypes.map((t) => [t, filtered.filter((e) => e.ev === t).length]))
  }, [res, pristine, eventTypes, filtered])

  const reset = () => {
    setEvents([])
    setActor(DEF_ACTOR)
    setFrom(DEF_FROM)
    setTo(DEF_TO)
    setTarget('')
  }

  const toggleEvent = (t) => setEvents((prev) => (prev.includes(t) ? prev.filter((x) => x !== t) : [...prev, t]))

  if (error) return <ErrorState detail={error} onRetry={retry} />
  if (!res) return <LoadingState message="감사로그를 불러오는 중…" />

  const total = res.total ?? res.items?.length ?? 0

  return (
    <div className="flex animate-[om-fadein_.3s_ease-out] flex-col gap-3.5">
      <div className="flex flex-wrap items-center gap-3">
        <div className="text-[21px] font-extrabold tracking-[-.3px] text-navy">감사로그</div>
        <span className="ml-auto rounded-full border border-line bg-line-soft px-3 py-[5px] text-[12.5px] font-bold text-slate">
          append-only · 수정·삭제 경로 없음
        </span>
      </div>

      <AuditFilterBar
        eventTypes={eventTypes}
        events={events}
        onToggleEvent={toggleEvent}
        actor={actor}
        onActor={setActor}
        from={from}
        onFrom={setFrom}
        to={to}
        onTo={setTo}
        target={target}
        onTarget={setTarget}
        sort={sort}
        onSort={setSort}
        onReset={reset}
        activeCount={filtered.length}
      />

      <div className="grid grid-cols-[7fr_3fr] items-start gap-4">
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2 px-1 text-[12.5px] font-bold text-slate">
            <span>
              타임라인 <span className="font-mono text-navy">{filtered.length}</span>건
              <span className="text-slate-light"> / 전체 </span>
              <span className="font-mono text-slate-light">{total}</span>
            </span>
            {target.trim() && (
              <span className="font-mono text-[12px] font-extrabold text-brand">대상 {target.trim()} 생애주기</span>
            )}
            <span className="ml-auto text-[12px] font-semibold text-slate-light">
              {sort === 'asc' ? '오래된 기록 → 최신 기록' : '최신 기록 → 오래된 기록'}
            </span>
          </div>
          <AuditTimeline items={filtered} />
        </div>
        <AuditEventTypeBars
          eventTypes={eventTypes}
          counts={counts}
          scopeLabel={pristine ? '전체 기간 기준 (필터 없음)' : '현재 필터 전체 기준'}
        />
      </div>
    </div>
  )
}

export default AuditLogPage

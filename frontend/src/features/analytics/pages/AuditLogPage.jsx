<<<<<<< Updated upstream
// 감사로그 — 디자인 v2 07 (append-only · 수정 · 삭제 경로 없음, 조회 전용 화면)
// 필터 4종은 전부 서버 파라미터로 넘긴다: event_type · actor_type · date_from · date_to (+ page·size)
import { useCallback, useEffect, useMemo, useState } from 'react'
import { getAuditLogs } from '../../../shared/api/analytics.js'
=======
import { useCallback, useEffect, useMemo, useState } from 'react'
import { getAuditLogs } from '../../../shared/api/analytics.js'
import ErrorState from '../../../shared/components/ErrorState.jsx'
import LoadingState from '../../../shared/components/LoadingState.jsx'
import AuditEventTypeBars from '../components/AuditEventTypeBars.jsx'
>>>>>>> Stashed changes
import AuditFilterBar from '../components/AuditFilterBar.jsx'
import AuditTimeline from '../components/AuditTimeline.jsx'

const DEF_FROM = '2026-06-01'
const DEF_TO = '2026-06-04'
const ALL = '전체'
// 타임라인은 기간 전체를 한 화면에 세운다 (시안에 페이지 나눔이 없다)
const PAGE_SIZE = 50

const PROOFS = [
  ['누가 승인했나', 'APPROVAL_DECIDED의 actor_type·actor_id'],
  ['언제 바뀌었나', 'occurred_at으로 상태 전이 시각 확인'],
  ['무엇이 바뀌었나', 'before / after 객체 비교'],
  ['전송됐나', 'ACTION_SENT 이벤트로 확인'],
  ['고칠 수 없다', 'append-only · UPDATE/DELETE 경로 없음'],
]

function AuditLogPage() {
  const [response, setResponse] = useState(null)
  const [error, setError] = useState(null)
  const [eventType, setEventType] = useState(ALL)
  const [actorType, setActorType] = useState(ALL)
  const [target, setTarget] = useState('')

  const q = target.trim()

  // 필터가 바뀌면 load가 새로 만들어져 useEffect가 다시 돈다.
  // setState는 전부 then/catch 안에서만 호출한다 (react-hooks/set-state-in-effect)
  const load = useCallback(() => {
<<<<<<< Updated upstream
    getAuditLogs({
      ...(eventType === ALL ? null : { event_type: eventType }),
      ...(actor === ALL ? null : { actor_type: actor }),
      date_from: DEF_FROM,
      date_to: DEF_TO,
      // TODO(api): entity_id 파라미터 제안 반영 대기 — 지금은 mock/클라이언트에서 거른다
      ...(q ? { entity_id: q } : null),
      page: 1,
      size: PAGE_SIZE,
    })
      .then(setRes)
      .catch((e) => setError(e.message))
  }, [eventType, actor, q])
=======
    const filter = {
      date_from: `${DEF_FROM}T00:00:00+09:00`,
      date_to: `${DEF_TO}T23:59:59+09:00`,
      size: 100,
      ...(eventType === ALL ? {} : { event_type: eventType }),
      ...(actorType === ALL ? {} : { actor_type: actorType }),
      ...(target.trim() ? { entity_id: target.trim() } : {}),
    }
    getAuditLogs(filter)
      .then((data) => {
        setResponse(data)
        setError(null)
      })
      .catch((requestError) => setError(requestError.message))
  }, [actorType, eventType, target])

>>>>>>> Stashed changes
  useEffect(() => {
    load()
  }, [load])

  const items = useMemo(
    () => [...(response?.items ?? [])].sort((a, b) => a.occurred_at.localeCompare(b.occurred_at) || a.audit_id - b.audit_id),
    [response],
  )

<<<<<<< Updated upstream
  const eventTypes = useMemo(() => res?.event_types ?? [], [res])

  // 응답은 시각 내림차순이라 화면에서 뒤집는다 + 같은 시각은 생애주기 흐름 랭크 보조 정렬
  const items = useMemo(() => {
    const list = res?.items ?? []
    return [...list].sort(
      (a, b) =>
        a.occurred_at.localeCompare(b.occurred_at) ||
        flowRank(a.event_type) - flowRank(b.event_type) ||
        a.audit_id - b.audit_id,
    )
  }, [res])

  // 집계는 현재 화면 slice가 아니라 같은 필터의 전체 집합 기준 — 응답 event_type_counts 를 쓴다
  const counts = useMemo(() => res?.event_type_counts ?? {}, [res])

  if (error) return <ErrorState detail={error} onRetry={retry} />
  if (!res) return <LoadingState message="감사로그를 불러오는 중…" />
=======
  if (error && !response) return <ErrorState detail={error} onRetry={load} />
  if (!response) return <LoadingState message="감사로그를 불러오는 중…" />
>>>>>>> Stashed changes

  const total = res.total ?? items.length
  const note =
    total > items.length
      ? `시각 오름차순 · 전체 ${total}건 중 ${items.length}건`
      : `시각 오름차순 · ${items.length}건`

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-16 items-center justify-between pb-1.5 pt-3.5">
        <div className="text-[22px] font-extrabold text-navy">감사로그</div>
        <div className="text-xs text-g1">append-only · 수정 · 삭제 경로 없음</div>
      </div>

      <AuditFilterBar
        eventTypes={response.event_types ?? []}
        eventType={eventType}
        onEventType={setEventType}
        actor={actorType}
        onActor={setActorType}
        target={target}
        onTarget={setTarget}
      />

      <div className="flex items-start gap-5">
<<<<<<< Updated upstream
        <AuditTimeline items={items} title={q ? `${q} 의 이력` : '전체 이력'} note={note} />
=======
        <AuditTimeline
          items={items}
          title={target.trim() ? `${target.trim()}의 이력` : '전체 이력'}
          note={`시각 오름차순 · ${items.length}건`}
        />
>>>>>>> Stashed changes

        <div className="flex w-[360px] flex-none flex-col gap-4">
          <AuditEventTypeBars eventTypes={response.event_types ?? []} counts={response.event_type_counts ?? {}} />
          <div className="rounded-[10px] border border-line bg-white p-5" style={{ borderLeft: '3px solid var(--color-red)' }}>
            <div className="mb-4 text-sm font-extrabold text-navy">이 화면이 증명하는 것</div>
            <div className="flex flex-col gap-4">
              {PROOFS.map(([title, detail]) => (
                <div key={title} className="flex gap-2.5">
                  <span className="mt-[5px] h-[7px] w-[7px] flex-none rounded-full bg-red" />
                  <span>
                    <span className="block text-[12.5px] font-bold text-ink">{title}</span>
                    <span className="mt-1 block text-[11.5px] text-g1">{detail}</span>
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

export default AuditLogPage

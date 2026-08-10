// 감사로그 — 디자인 v2 07 (append-only · 수정 · 삭제 경로 없음, 조회 전용 화면)
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
const ALL = '전체'

// 시각이 분 단위인 항목은 같은 분 안에서 생애주기 흐름 순서로 보조 정렬한다
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

// "이 화면이 증명하는 것" — 디자인 v2 07 시안 5항목 그대로
const PROOFS = [
  ['누가 승인했나', 'ACTION_APPROVED 의 주체가 HUMAN · bang'],
  ['언제 바뀌었나', 'PENDING → APPROVED 시각까지'],
  ['무엇이 바뀌었나', 'before / after 로 상태 전이 그대로'],
  ['전송됐나', 'ACTION_SENT 가 남으면 n8n 이 받았다는 뜻'],
  ['고칠 수 없다', 'append-only · 서비스에 UPDATE · DELETE 경로 없음'],
]

function AuditLogPage() {
  const [res, setRes] = useState(null)
  const [error, setError] = useState(null)
  const [eventType, setEventType] = useState(ALL)
  const [actor, setActor] = useState(ALL)
  const [target, setTarget] = useState('')

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
  const q = target.trim().toLowerCase()

  // 서버 명세에 대상 ID 필터가 없어 조회 결과 전체에 클라이언트 필터를 적용한다
  const filtered = useMemo(() => {
    const items = res?.items ?? []
    const hit = items.filter((e) => {
      const { date } = isoToParts(e.at)
      return (
        (eventType === ALL || e.ev === eventType) &&
        (actor === ALL || e.ac === actor) &&
        date >= DEF_FROM &&
        date <= DEF_TO &&
        // TODO(api): entity_id 파라미터 제안 반영 대기
        (!q || String(e.entity_id).toLowerCase().includes(q))
      )
    })
    // 시각 오름차순 기본 + 같은 시각은 생애주기 흐름 랭크 보조 정렬
    return [...hit].sort((a, b) => a.at.localeCompare(b.at) || flowRank(a.ev) - flowRank(b.ev))
  }, [res, eventType, actor, q])

  const pristine = eventType === ALL && actor === ALL && q === ''

  // 집계는 현재 화면 slice가 아니라 같은 필터의 전체 집합 기준이다.
  // 필터가 기본값이면 응답의 event_type_counts를 그대로 사용한다.
  const counts = useMemo(() => {
    if (!res) return {}
    if (pristine) return res.event_type_counts ?? {}
    return Object.fromEntries(eventTypes.map((t) => [t, filtered.filter((e) => e.ev === t).length]))
  }, [res, pristine, eventTypes, filtered])

  if (error) return <ErrorState detail={error} onRetry={retry} />
  if (!res) return <LoadingState message="감사로그를 불러오는 중…" />

  return (
    <div className="animate-[om-fadein_.3s_ease-out]">
      <div className="flex min-h-16 items-center justify-between pb-1.5 pt-3.5">
        <div className="text-[22px] font-extrabold text-navy">감사로그</div>
        <div className="text-xs text-g1">append-only · 수정 · 삭제 경로 없음</div>
      </div>

      <AuditFilterBar
        eventTypes={eventTypes}
        eventType={eventType}
        onEventType={setEventType}
        actor={actor}
        onActor={setActor}
        target={target}
        onTarget={setTarget}
      />

      <div className="flex items-start gap-5">
        <AuditTimeline
          items={filtered}
          title={q ? `${target.trim()} 의 이력` : '전체 이력'}
          note={`시각 오름차순 · ${filtered.length}건`}
        />

        <div className="flex w-[360px] flex-none flex-col gap-4">
          <AuditEventTypeBars eventTypes={eventTypes} counts={counts} />

          {/* 좌측 3px 적 보더 카드 — 시안 그대로 */}
          <div className="rounded-[10px] border border-line bg-white p-5" style={{ borderLeft: '3px solid var(--color-red)' }}>
            <div className="mb-4 text-sm font-extrabold text-navy">이 화면이 증명하는 것</div>
            <div className="flex flex-col gap-4">
              {PROOFS.map(([t, d]) => (
                <div key={t} className="flex gap-2.5">
                  <span className="mt-[5px] h-[7px] w-[7px] flex-none rounded-full bg-red" />
                  <span>
                    <span className="block text-[12.5px] font-bold text-ink">{t}</span>
                    <span className="mt-1 block text-[11.5px] text-g1">{d}</span>
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

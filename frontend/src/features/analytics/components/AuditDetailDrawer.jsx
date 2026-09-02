import { useEffect } from 'react'
import { Link } from 'react-router-dom'
import { fmtDateTime, fmtTime } from '../../../shared/api/format.js'
import { EventMark } from './AuditTable.jsx'
import {
  PHASE_DOT,
  actorLabel,
  changeRows,
  describeEvent,
  entityLabel,
  eventLabel,
  eventPhase,
  isFailure,
  primaryTargetOf,
  runKeyOf,
  runTimeline,
} from './auditModel.js'

// 감사 이벤트 상세 드로어 — 행 클릭으로 열린다 (V5-D-1.4 "상세 before·after").
// 독자는 개발자가 아니라 공장 관리자다. 위에서부터
//   무슨 일이 있었나(한 문장) → 무엇이 바뀌었나(항목별 전→후) → 이 실행의 앞뒤 흐름 → Agent 화면 링크
// 순서로 읽히게 하고, 원본 JSON 은 감사 증빙용으로 접어 둔다.

const Section = ({ title, children }) => (
  <section>
    <div className="mb-2 text-[11px] font-semibold text-g2">{title}</div>
    {children}
  </section>
)

function Changes({ rows }) {
  if (rows.length === 0) return <div className="text-[12.5px] text-faint">기록된 변경 항목이 없습니다.</div>
  return (
    <dl className="grid grid-cols-[max-content_1fr] gap-x-5 gap-y-2 text-[12.5px]">
      {rows.map((r) => (
        <div key={r.key} className="contents">
          <dt className="text-g2">{r.label}</dt>
          <dd className="m-0 text-ink">
            {r.before != null && r.after != null && r.changed ? (
              <>
                <span className="text-g2">{r.before}</span> <span className="text-faint">→</span>{' '}
                <span className="font-medium">{r.after}</span>
              </>
            ) : (
              <span className={r.after != null ? 'font-medium' : 'text-g2'}>{r.after ?? r.before ?? '—'}</span>
            )}
          </dd>
        </div>
      ))}
    </dl>
  )
}

function Timeline({ items, current, onSelect }) {
  if (items.length < 2) return null
  return (
    <ol className="m-0 flex list-none flex-col p-0">
      {items.map((x, i) => {
        const on = x.audit_id === current.audit_id
        const fail = isFailure(x.event_type)
        return (
          <li key={x.audit_id} className="relative flex items-center gap-3 py-1.5">
            {i < items.length - 1 && <span className="absolute left-[3.5px] top-[22px] h-[calc(100%-14px)] w-px bg-navy-1" aria-hidden="true" />}
            <span className="relative h-2 w-2 flex-none rounded-full" style={{ background: PHASE_DOT[eventPhase(x.event_type)] }} />
            <button
              type="button"
              onClick={() => onSelect?.(x)}
              className={`flex flex-1 cursor-pointer items-baseline gap-3 rounded-md px-2 py-1 text-left text-[12.5px] transition-colors hover:bg-soft ${
                on ? 'bg-row-sel' : ''
              }`}
            >
              <span className="font-mono text-[11.5px] text-g2">{fmtTime(x.occurred_at)}</span>
              <span className={`${on ? 'font-semibold' : ''} ${fail ? 'text-fail' : 'text-ink'}`}>{eventLabel(x.event_type)}</span>
              {on && <span className="ml-auto text-[10.5px] text-g2">지금 보는 기록</span>}
            </button>
          </li>
        )
      })}
    </ol>
  )
}

function AuditDetailDrawer({ event, items = [], runContext, onClose, onSelect }) {
  useEffect(() => {
    if (!event) return undefined
    const onKey = (ev) => ev.key === 'Escape' && onClose?.()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [event, onClose])

  if (!event) return null
  const fail = isFailure(event.event_type)
  const target = primaryTargetOf(event, runContext)
  const runKey = runKeyOf(event)
  const ctx = runKey ? runContext?.get(runKey) : null
  const rows = changeRows(event)
  const timeline = runTimeline(items, event)

  return (
    <>
      <div className="fixed inset-0 z-40 bg-navy/10" onClick={onClose} aria-hidden="true" />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="감사 이벤트 상세"
        className="fixed inset-y-0 right-0 z-50 flex w-[460px] flex-col border-l border-line bg-white shadow-[-16px_0_40px_rgba(28,49,80,0.12)] animate-[om-fadein_.2s_ease-out]"
      >
        <header className={`border-b border-line px-6 pb-5 pt-4 border-t-[3px] ${fail ? 'border-t-fail' : 'border-t-transparent'}`}>
          <div className="flex items-start justify-between gap-3">
            <EventMark type={event.event_type} className="text-[13px]" />
            <button
              type="button"
              onClick={onClose}
              className="-mr-2 -mt-1 inline-flex h-7 cursor-pointer items-center rounded-md px-2 text-[12px] text-g2 hover:bg-soft hover:text-navy"
            >
              닫기
            </button>
          </div>
          <div className={`mt-3 text-[16px] font-bold leading-tight text-navy ${target.mono ? 'font-mono' : ''}`}>{target.primary}</div>
          <div className="mt-2 flex items-baseline gap-4 text-[12px]">
            <span className="font-mono text-g1">{fmtDateTime(event.occurred_at)}</span>
            <span className="text-g2">
              {actorLabel(event.actor_type)}
              {event.actor_id ? <span className="font-mono"> {event.actor_id}</span> : null}
            </span>
          </div>
        </header>

        <div className="flex flex-1 flex-col gap-6 overflow-y-auto px-6 py-5">
          <p className="m-0 text-[13.5px] leading-[1.65] text-ink">{describeEvent(event, ctx)}</p>

          <Section title="변경 내용">
            <Changes rows={rows} />
          </Section>

          {timeline.length >= 2 && (
            <Section title="이 실행의 흐름">
              <Timeline items={timeline} current={event} onSelect={onSelect} />
            </Section>
          )}

          {runKey && event.entity_type === 'AGENT_RUN' && (
            <Link
              to={`/agent-runs/${encodeURIComponent(runKey)}`}
              className="inline-flex w-fit items-center gap-1.5 text-[12.5px] font-semibold text-blue hover:text-blue-hover"
            >
              Agent 분석 화면에서 근거·조치 보기 →
            </Link>
          )}

          <details className="group mt-auto">
            <summary className="cursor-pointer list-none text-[11.5px] text-g2 hover:text-navy">
              원본 데이터 <span className="text-faint group-open:hidden">보기</span>
              <span className="hidden text-faint group-open:inline">접기</span>
            </summary>
            <div className="mt-2 flex flex-col gap-2 text-[11px]">
              <div className="text-g2">
                {entityLabel(event.entity_type)} <span className="font-mono">{event.entity_id}</span>
              </div>
              <pre className="m-0 overflow-x-auto whitespace-pre-wrap break-all rounded-lg bg-soft px-3 py-2.5 font-mono text-[11px] leading-[1.6] text-g1">
                {JSON.stringify({ before: event.before, after: event.after, detail: event.detail }, null, 2)}
              </pre>
            </div>
          </details>
        </div>

        <footer className="border-t border-cell-line px-6 py-3 text-[11px] text-faint">
          기록 번호 <span className="font-mono">{event.audit_id}</span> · 이 기록은 수정·삭제되지 않습니다
        </footer>
      </aside>
    </>
  )
}

export default AuditDetailDrawer

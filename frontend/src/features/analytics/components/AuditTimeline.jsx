// 감사로그 타임라인 — append-only 기록의 세로 축 뷰 (조회 전용, 상태 변경 UI 없음)
import { Fragment } from 'react'
import { isoToParts } from '../../../shared/api/format.js'
import EmptyState from '../../../shared/components/EmptyState.jsx'

// 이벤트명 색: FAILED/REJECTED → oos · SENT/COMPLETED/APPROVED → ok · REQUESTED → ooc · 그 외 brand
const evTone = (ev) =>
  ev.includes('FAILED') || ev.includes('REJECTED')
    ? { bg: '#FEE2E2', fg: '#DC2626' }
    : ev.includes('SENT') || ev.includes('COMPLETED') || ev.includes('APPROVED')
      ? { bg: '#DCFCE7', fg: '#16A34A' }
      : ev.includes('REQUESTED')
        ? { bg: '#FEF3C7', fg: '#D97706' }
        : { bg: '#EDF2FA', fg: '#1E5FC2' }

// 주체 배지: AGENT 파랑 계열 · HUMAN navy 솔리드 · SYSTEM 회색
const acTone = (ac) =>
  ac === 'HUMAN'
    ? { bg: '#0F2A5C', fg: '#FFFFFF' }
    : ac === 'AGENT'
      ? { bg: '#DBEAFE', fg: '#1E5FC2' }
      : { bg: '#F1F5F9', fg: '#475569' }

function AuditChangeChips({ before, after }) {
  const keys = [...Object.keys(after ?? {}), ...Object.keys(before ?? {}).filter((k) => !(k in (after ?? {})))]
  if (keys.length === 0) {
    return <span className="text-[12.5px] font-semibold text-slate-light">상태 변경 없음 (기록 전용 이벤트)</span>
  }
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5">
      {keys.map((k) => {
        const hasBefore = before != null && k in before
        return (
          <span key={k} className="inline-flex items-center gap-1.5">
            {hasBefore ? (
              <span
                className="rounded-md px-2 py-[3px] font-mono text-[11.5px] font-extrabold"
                style={{ background: '#F1F5F9', color: '#475569' }}
              >
                {k}: {String(before[k])}
              </span>
            ) : (
              // 신규 생성 건 — 이전 상태가 존재하지 않는다
              <span
                className="rounded-md border border-dashed px-2 py-[3px] text-[11.5px] font-bold"
                style={{ borderColor: '#CBD5E1', color: '#64748B' }}
              >
                before 없음
              </span>
            )}
            <span aria-hidden className="font-mono text-[12px] font-extrabold text-slate-light">
              →
            </span>
            <span
              className="rounded-md px-2 py-[3px] font-mono text-[11.5px] font-extrabold"
              style={{ background: '#DCFCE7', color: '#16A34A' }}
            >
              {k}: {after && k in after ? String(after[k]) : '—'}
            </span>
          </span>
        )
      })}
    </div>
  )
}

function AuditTimelineItem({ item }) {
  const { date, time } = isoToParts(item.at)
  const ev = evTone(item.ev)
  const ac = acTone(item.ac)
  return (
    <li className="relative pl-8">
      {/* 타임라인 노드 */}
      <span
        aria-hidden
        className="absolute left-[3px] top-[18px] h-[13px] w-[13px] rounded-full border-2 border-white"
        style={{ background: ev.fg, boxShadow: `0 0 0 2px ${ev.bg}` }}
      />
      <div className="my-1.5 rounded-xl border border-line-soft bg-white px-3.5 py-3 transition-colors duration-[120ms] hover:border-line hover:bg-page">
        <div className="flex flex-wrap items-center gap-2">
          <span
            className="rounded-md px-2 py-[3px] font-mono text-[11.5px] font-extrabold"
            style={{ background: ev.bg, color: ev.fg }}
          >
            {item.ev}
          </span>
          <span
            className="inline-flex items-center gap-1.5 rounded-md px-2 py-[3px] text-[11.5px] font-extrabold"
            style={{ background: ac.bg, color: ac.fg }}
          >
            {item.ac}
            <span className="font-mono font-bold opacity-85">{item.actor}</span>
          </span>
          <span className="font-mono text-[12.5px] font-semibold text-slate">
            {item.entity} <span className="font-extrabold text-navy">{item.entity_id}</span>
          </span>
          <span className="ml-auto font-mono text-[12.5px] font-bold text-slate-light">
            {date} {time}
          </span>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <span className="text-[13px] font-semibold text-ink">{item.summary}</span>
          <AuditChangeChips before={item.before} after={item.after} />
        </div>
      </div>
    </li>
  )
}

function AuditTimeline({ items }) {
  if (items.length === 0) {
    return (
      <div className="rounded-xl border border-line bg-white shadow-[0_1px_3px_rgba(15,42,92,.05)]">
        <EmptyState title="조건에 맞는 감사 기록이 없습니다" description="기간·이벤트·주체·대상 ID 필터를 조정해 주세요." />
      </div>
    )
  }
  // 날짜가 바뀌는 지점에 구분 헤더를 넣어 생애주기 흐름이 끊기지 않게 한다
  let prevDate = null
  return (
    <div className="rounded-xl border border-line bg-white shadow-[0_1px_3px_rgba(15,42,92,.05)]">
      <div className="max-h-[calc(100vh-330px)] min-h-[320px] overflow-y-auto px-[18px] py-3.5">
        <ol className="relative m-0 list-none p-0">
          {/* 세로 타임라인 축 */}
          <span aria-hidden className="absolute bottom-2 left-[9px] top-2 w-px bg-line" />
          {items.map((item) => {
            const { date } = isoToParts(item.at)
            const head = date !== prevDate ? date : null
            prevDate = date
            return (
              <Fragment key={`${item.ev}-${item.entity_id}-${item.at}`}>
                {head && (
                  <li className="relative list-none pl-8">
                    <span
                      aria-hidden
                      className="absolute left-[5px] top-[13px] h-[9px] w-[9px] rounded-sm bg-line-input"
                    />
                    <div className="mt-2.5 pb-0.5 font-mono text-[12px] font-extrabold tracking-[.3px] text-slate-light">
                      {head}
                    </div>
                  </li>
                )}
                <AuditTimelineItem item={item} />
              </Fragment>
            )
          })}
        </ol>
      </div>
    </div>
  )
}

export default AuditTimeline

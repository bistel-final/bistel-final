<<<<<<< Updated upstream
// 감사로그 타임라인 — append-only 기록의 세로 dot·rail 뷰 (조회 전용, 쓰기 UI 없음). 디자인 v2 07.
// 명세 AuditLogItem: audit_id · occurred_at(ISO) · actor_type · actor_id · event_type ·
// entity_type · entity_id · before/after(dict|null) · detail
=======
>>>>>>> Stashed changes
import Badge from '../../../shared/components/ui/Badge.jsx'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import EmptyState from '../../../shared/components/EmptyState.jsx'
import StateBox from '../../../shared/components/ui/StateBox.jsx'
import { actorVariant } from '../../../shared/components/ui/statusStyles.js'

const dotClass = (eventType) =>
  eventType.includes('APPROVED') || eventType.includes('SENT')
    ? 'bg-green'
    : eventType.includes('REJECTED') || eventType.includes('FAILED')
      ? 'bg-red'
      : eventType.includes('REQUESTED')
        ? 'bg-amber'
        : 'bg-blue'

const fmtAt = (iso) => {
  const [date, rest] = String(iso ?? '').split('T')
  return date && rest ? `${date.slice(5)} ${rest.slice(0, 8)}` : '—'
}

const renderState = (value) => {
  if (value == null) return null
  if (typeof value === 'string') return value
  return JSON.stringify(value, null, 2)
}

function AuditTimeline({ items, title, note }) {
  return (
    <Card className="min-w-0 flex-1">
      <CardHeader title={title} note={note} />
      {items.length === 0 ? (
        <div className="px-5 pb-5">
          <EmptyState title="조건에 맞는 감사 기록이 없습니다" description="이벤트·주체·대상 ID 필터를 조정해 주세요." />
        </div>
      ) : (
        <div className="flex flex-col px-5 pb-5 pt-1">
<<<<<<< Updated upstream
          {items.map((e, i) => (
            <div key={e.audit_id} className="flex gap-4 pb-3.5">
              <div className="flex w-3 flex-none flex-col items-center">
                <span className={`mt-4 h-3 w-3 flex-none rounded-full ${dotClass(e.event_type)}`} />
                {i < items.length - 1 && <span className="mt-1 w-0.5 flex-1 bg-line" />}
=======
          {items.map((event, index) => (
            <div key={event.audit_id} className="flex gap-4 pb-3.5">
              <div className="flex w-3 flex-none flex-col items-center">
                <span className={`mt-4 h-3 w-3 flex-none rounded-full ${dotClass(event.event_type)}`} />
                {index < items.length - 1 && <span className="mt-1 w-0.5 flex-1 bg-line" />}
>>>>>>> Stashed changes
              </div>
              <div
                className="min-w-0 flex-1 rounded-[10px] border border-line px-[18px] py-4"
<<<<<<< Updated upstream
                style={{ background: e.actor_type === 'HUMAN' ? '#FBF8F8' : '#FBFCFD' }}
              >
                <div className="flex items-center gap-3">
                  <span className="font-mono text-[13px] font-extrabold text-navy">{e.event_type}</span>
                  <Badge variant={actorVariant(e.actor_type)}>{e.actor_type}</Badge>
                  <span className="ml-auto font-mono text-[11px] text-g1">{fmtAt(e.occurred_at)}</span>
                </div>
                <div className="mt-2.5 flex gap-6">
                  <span className="font-mono text-[11.5px] text-g1">
                    {e.entity_type} · {e.entity_id}
                  </span>
                  <span className="text-[11.5px] text-g1">
                    주체 <span className="font-mono font-semibold text-ink">{e.actor_id}</span>
=======
                style={{ background: event.actor_type === 'HUMAN' ? '#FBF8F8' : '#FBFCFD' }}
              >
                <div className="flex items-center gap-3">
                  <span className="font-mono text-[13px] font-extrabold text-navy">{event.event_type}</span>
                  <Badge variant={actorVariant(event.actor_type)}>{event.actor_type}</Badge>
                  <span className="ml-auto font-mono text-[11px] text-g1">{fmtAt(event.occurred_at)}</span>
                </div>
                <div className="mt-2.5 flex gap-6">
                  <span className="font-mono text-[11.5px] text-g1">
                    {event.entity_type} · {event.entity_id}
                  </span>
                  <span className="text-[11.5px] text-g1">
                    주체 <span className="font-mono font-semibold text-ink">{event.actor_id ?? '—'}</span>
>>>>>>> Stashed changes
                  </span>
                </div>
                {/* before 가 null 이면 after 만 — 사유는 응답의 detail 문구를 그대로 쓴다 */}
                <div className="mt-3 flex flex-wrap items-center gap-3.5">
                  {event.before != null && (
                    <>
                      <StateBox tone="red">
                        <pre className="whitespace-pre-wrap font-mono text-[10.5px]">{renderState(event.before)}</pre>
                      </StateBox>
                      <span className="text-g2">→</span>
                    </>
                  )}
<<<<<<< Updated upstream
                  <StateBox tone="green">{e.after}</StateBox>
                  {e.detail && <span className="text-[11.5px] text-g1">{e.detail}</span>}
=======
                  {event.after != null && (
                    <StateBox tone="green">
                      <pre className="whitespace-pre-wrap font-mono text-[10.5px]">{renderState(event.after)}</pre>
                    </StateBox>
                  )}
                  {event.detail && <span className="text-[11.5px] text-g1">{event.detail}</span>}
>>>>>>> Stashed changes
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  )
}

export default AuditTimeline

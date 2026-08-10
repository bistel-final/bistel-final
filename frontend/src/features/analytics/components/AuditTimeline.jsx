// 감사로그 타임라인 — append-only 기록의 세로 dot·rail 뷰 (조회 전용, 쓰기 UI 없음). 디자인 v2 07.
// 명세 AuditLogItem: audit_id · occurred_at(ISO) · actor_type · actor_id · event_type ·
// entity_type · entity_id · before/after(dict|null) · detail
import Badge from '../../../shared/components/ui/Badge.jsx'
import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import StateBox from '../../../shared/components/ui/StateBox.jsx'
import { actorVariant } from '../../../shared/components/ui/statusStyles.js'
import EmptyState from '../../../shared/components/EmptyState.jsx'

// dot 색 — 이벤트: APPROVED/SENT → green · REQUESTED → amber · 그 외 → blue
const dotClass = (ev) =>
  ev.includes('APPROVED') || ev.includes('SENT') ? 'bg-green' : ev.includes('REQUESTED') ? 'bg-amber' : 'bg-blue'

// "MM-DD HH:mm[:ss]" — 분 단위 실측(ISO 변환 시 :00 패딩)은 초를 표기하지 않는다.
// TODO(data): audit_log CSV로 초 단위 확보 시 패딩 판별 없이 그대로 노출.
const fmtAt = (iso) => {
  const [date, rest] = String(iso).split('T')
  const time = (rest ?? '').slice(0, 8)
  return `${date.slice(5)} ${time.endsWith(':00') ? time.slice(0, 5) : time}`
}

function AuditTimeline({ items, title, note }) {
  return (
    <Card className="min-w-0 flex-1">
      <CardHeader title={title} note={note} />
      {items.length === 0 ? (
        <div className="px-5 pb-5">
          <EmptyState
            title="조건에 맞는 감사 기록이 없습니다"
            description="이벤트·주체·대상 ID 필터를 조정해 주세요."
          />
        </div>
      ) : (
        <div className="flex flex-col px-5 pb-5 pt-1">
          {items.map((e, i) => (
            <div key={e.audit_id} className="flex gap-4 pb-3.5">
              <div className="flex w-3 flex-none flex-col items-center">
                <span className={`mt-4 h-3 w-3 flex-none rounded-full ${dotClass(e.event_type)}`} />
                {i < items.length - 1 && <span className="mt-1 w-0.5 flex-1 bg-line" />}
              </div>
              {/* 항목 카드 배경 — 시안 값: HUMAN 이벤트 #FBF8F8 · 그 외 #FBFCFD */}
              <div
                className="min-w-0 flex-1 rounded-[10px] border border-line px-[18px] py-4"
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
                  </span>
                </div>
                {/* before 가 null 이면 after 만 — 사유는 응답의 detail 문구를 그대로 쓴다 */}
                <div className="mt-3 flex flex-wrap items-center gap-3.5">
                  {e.before && (
                    <>
                      <StateBox tone="red">{e.before}</StateBox>
                      <span className="text-g2">→</span>
                    </>
                  )}
                  <StateBox tone="green">{e.after}</StateBox>
                  {e.detail && <span className="text-[11.5px] text-g1">{e.detail}</span>}
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

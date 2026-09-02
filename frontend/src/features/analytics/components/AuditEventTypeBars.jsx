import { Card, CardHeader } from '../../../shared/components/ui/Card.jsx'
import { eventHex, eventLabel } from './auditModel.js'

// 유형별 집계 — 라이트 시안 7번 좌측 270px. 전 유형 가로바 (값/최대 비율), 0건은 dim.
// 라벨은 한글 사전(eventLabel), 색은 4톤 틴트 팔레트(eventHex) — 표의 배지와 같은 사전·같은 색.

function AuditEventTypeBars({ eventTypes, counts, total }) {
  const max = Math.max(1, ...eventTypes.map((t) => counts[t] ?? 0))
  return (
    <Card className="w-[270px] flex-none">
      <CardHeader title="유형별 집계" note={`${total}건`} />
      <div className="flex flex-col gap-3 px-4 pb-4">
        {eventTypes.map((t) => {
          const n = counts[t] ?? 0
          const pct = n === 0 ? 4 : Math.max(6, (n / max) * 100)
          return (
            <div key={t}>
              <div className="flex items-baseline justify-between gap-2">
                <span className={`truncate text-[12px] font-semibold ${n === 0 ? 'text-faint' : 'text-g1'}`} title={t}>
                  {eventLabel(t)}
                </span>
                <span className={`font-mono text-[12px] font-bold ${n === 0 ? 'text-faint' : 'text-navy'}`}>{n}</span>
              </div>
              <div className="mt-1 h-[8px] overflow-hidden rounded-full bg-cell-line">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${pct}%`, background: n === 0 ? 'var(--color-tint-gray-line)' : eventHex(t), opacity: n === 0 ? 1 : 0.85 }}
                />
              </div>
            </div>
          )
        })}
      </div>
    </Card>
  )
}

export default AuditEventTypeBars
